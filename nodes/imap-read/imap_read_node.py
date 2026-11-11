"""Read-only IMAP ingestion, adapted from the existing mail-extension reader."""
import hashlib
import imaplib
import re
import ssl
import zipfile
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from cli_runtime import register_node_class, run_registered_node_from_cli
from ingestion_io import descriptor, integer, json_bytes, temporary


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode_message(raw, uid, validity, mailbox, folder, rules, max_attachment_bytes,
                   max_attachments):
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    senders = [a.casefold() for _, a in getaddresses(msg.get_all("from", []))]
    subject = str(msg.get("subject", ""))
    if rules and (len(senders) != 1 or not any(senders[0] == r["sender"].casefold()
            and (not r["subjects"] or any(s.casefold() in subject.casefold() for s in r["subjects"])) for r in rules)):
        return None, []
    issues = ["mime_" + type(d).__name__ for d in msg.defects]
    sent = None
    try:
        sent = parsedate_to_datetime(str(msg.get("date", "")))
        if sent.tzinfo is None:
            raise ValueError()
        sent = sent.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        issues.append("invalid_sent_at")
    bodies, html, attachments, files = [], [], [], []
    prefix = f"messages/{uid}"

    def visit(part, part_id):
        filename = part.get_filename()
        is_attachment = bool(filename) or part.get_content_disposition() == "attachment"
        if part.is_multipart() and not is_attachment:
            for n, child in enumerate(part.iter_parts(), 1):
                visit(child, f"{part_id}.{n}")
            return
        if part.is_multipart():
            payload = b"\r\n".join(p.as_bytes(policy=policy.SMTP) for p in part.get_payload())
        else:
            payload = part.get_payload(decode=True) or b""
        issues.extend("mime_" + type(d).__name__ for d in part.defects)
        if is_attachment or part.get_content_maintype() != "text":
            if len(payload) > max_attachment_bytes:
                raise ValueError("attachment_size_limit")
            if len(attachments) >= max_attachments:
                raise ValueError("attachment_count_limit")
            entry = f"{prefix}/parts/{part_id}.bin"
            attachment_id = f"{validity}:{uid}:{part_id}"
            attachments.append({"attachment_id": attachment_id, "part_id": part_id,
                "filename": filename or f"part-{part_id}.bin", "archive_entry": entry,
                "content_type": part.get_content_type(), "sha256": sha(payload),
                "byte_size": len(payload), "content_id": str(part.get("Content-ID", ""))})
            files.append((entry, payload))
        elif part.get_content_type() in ("text/plain", "text/html"):
            try:
                text = payload.decode(part.get_content_charset() or "utf-8")
            except (UnicodeError, LookupError):
                text = payload.decode("utf-8", errors="replace")
                issues.append("body_decode_replaced")
            (bodies if part.get_content_type() == "text/plain" else html).append(text)

    visit(msg, "1")
    if not bodies and html:
        parser = PlainHTML()
        parser.feed("\n".join(html))
        bodies = ["".join(parser.parts)]
    original_entry = f"{prefix}/original.eml"
    files.append((original_entry, raw))
    message = {"source_key": f"{mailbox}/{folder}/{validity}/{uid}",
        "mailbox": mailbox, "folder": folder, "uidvalidity": validity, "uid": uid,
        "message_id": str(msg.get("message-id", "")), "sender": senders[0] if len(senders) == 1 else None,
        "senders": senders, "subject": subject,
        "sent_at": sent, "ingested_at": datetime.now(timezone.utc).isoformat(),
        "body_text": "\n".join(bodies), "body_html": "\n".join(html),
        "raw_sha256": sha(raw), "raw_byte_size": len(raw), "original_entry": original_entry,
        "attachments": attachments, "issues": sorted(set(issues))}
    return message, files


def _validity(client, key):
    _, data = client.response(key)
    if not data or not data[0] or not re.fullmatch(rb"\d+", data[0]):
        raise RuntimeError("imap_missing_" + key.lower())
    return int(data[0])


def read_imap(credentials, rules=None, folder="INBOX", checkpoint=None, batch_size=25,
              max_message_bytes=40_000_000, max_attachment_bytes=30_000_000,
              max_batch_bytes=80_000_000, max_attachments=100, max_metadata_bytes=262144,
              client_factory=None):
    client, archive_path = None, None
    try:
        batch_size = integer(batch_size, "batch_size", 25, 500)
        max_message_bytes = integer(max_message_bytes, "max_message_bytes", 40_000_000, 100_000_000)
        max_attachment_bytes = integer(max_attachment_bytes, "max_attachment_bytes", 30_000_000, 100_000_000)
        max_batch_bytes = integer(max_batch_bytes, "max_batch_bytes", 80_000_000, 500_000_000)
        max_attachments = integer(max_attachments, "max_attachments", 100, 1000)
        max_metadata_bytes = integer(max_metadata_bytes, "max_metadata_bytes", 262144, 524288)
        rules = [] if rules is None else rules
        if not isinstance(rules, list) or any(
            not isinstance(r, dict) or not isinstance(r.get("sender"), str) or not r["sender"]
            or not isinstance(r.get("subjects"), list)
            or any(not isinstance(s, str) or not s for s in r["subjects"]) for r in rules):
            raise ValueError("invalid_rules")
        if not isinstance(credentials, dict) or any(not isinstance(credentials.get(k), str)
                or not credentials[k] for k in ("host", "username", "password")):
            raise ValueError("invalid_credentials")
        if not isinstance(folder, str) or not folder or any(c in folder for c in "\r\n\x00"):
            raise ValueError("invalid_folder")
        last = int((checkpoint or {}).get("last_committed_uid", 0))
        if last < 0 or (checkpoint and (not checkpoint.get("uidvalidity") or last > 4294967295)):
            raise ValueError("invalid_checkpoint")
        port = integer(credentials.get("port"), "port", 993, 65535)
        factory = client_factory or imaplib.IMAP4_SSL
        client = factory(credentials["host"], port, ssl_context=ssl.create_default_context(), timeout=60)
        client.login(credentials["username"], credentials["password"])
        # imaplib.select(readonly=True) issues EXAMINE. Quote the mailbox as one argument.
        quoted = '"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"'
        status, _ = client.select(quoted, readonly=True)
        if status != "OK":
            raise RuntimeError("imap_examine_failed")
        validity = str(_validity(client, "UIDVALIDITY"))
        highest = _validity(client, "UIDNEXT") - 1
        base = {"messages": [], "archive": None, "candidate_checkpoint": None,
                "checkpoint_committed": False, "has_more": False}
        if checkpoint and str(checkpoint["uidvalidity"]) != validity:
            return dict(base, status="recovery_required", diagnostics={"code": "uidvalidity_changed",
                "previous_uidvalidity": str(checkpoint["uidvalidity"]), "uidvalidity": validity})
        if last > highest:
            return dict(base, status="recovery_required", diagnostics={"code": "checkpoint_ahead_of_mailbox"})
        # Bounded UID window: SEARCH never returns an entire mailbox history.
        upper = min(highest, last + 1000)
        uids = []
        if upper > last:
            status, found = client.uid("search", None, "UID", f"{last + 1}:{upper}")
            if status != "OK":
                raise RuntimeError("imap_search_failed")
            uids = sorted({int(v) for v in (found[0] or b"").split() if last < int(v) <= upper})
        messages, summaries, total, scanned, blocked = [], [], 0, last, None
        metadata_size = 0
        archive_path = temporary(".zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for uid in uids[:batch_size]:
                status, parts = client.uid("fetch", str(uid), "(UID RFC822.SIZE INTERNALDATE)")
                meta = b" ".join(p for p in parts if isinstance(p, bytes))
                match = re.search(rb"RFC822.SIZE\s+(\d+)", meta)
                if status != "OK" or not match:
                    raise RuntimeError("imap_size_failed")
                size = int(match[1])
                if size > max_message_bytes or size > max_batch_bytes:
                    blocked = {"code": "message_size_limit", "uid": uid}
                    break
                if total + size > max_batch_bytes:
                    blocked = {"code": "batch_boundary", "uid": uid}
                    break
                raw = bytearray()
                # Partial PEEK caps every literal before imaplib allocates it.
                while len(raw) < size:
                    count = min(65536, size - len(raw))
                    status, fetched = client.uid("fetch", str(uid), f"(BODY.PEEK[]<{len(raw)}.{count}>)")
                    chunks = [p[1] for p in fetched if isinstance(p, tuple) and isinstance(p[1], bytes)]
                    chunk = b"".join(chunks)
                    if status != "OK" or not chunk or len(chunk) > count:
                        raise RuntimeError("imap_body_failed")
                    raw.extend(chunk)
                total += len(raw)
                try:
                    message, files = decode_message(bytes(raw), uid, validity, credentials["username"],
                        folder, rules, max_attachment_bytes, max_attachments)
                except ValueError as exc:
                    if str(exc) not in ("attachment_size_limit", "attachment_count_limit"):
                        raise
                    blocked = {"code": str(exc), "uid": uid}
                    break
                if message:
                    internal = re.search(rb'INTERNALDATE "([^"]+)"', meta)
                    message["internal_date"] = internal[1].decode("ascii") if internal else None
                    summary = {key: message[key] for key in ("uid", "source_key", "sender", "subject",
                        "message_id", "sent_at", "internal_date", "raw_sha256", "original_entry", "attachments", "issues")}
                    entry = f"messages/{uid}/metadata.json"
                    summary["metadata_entry"] = entry
                    metadata_size += len(json_bytes(summary))
                    if metadata_size > max_metadata_bytes:
                        blocked = {"code": "metadata_size_limit", "uid": uid}
                        break
                    for entry, content in files:
                        archive.writestr(entry, content)
                    entry = f"messages/{uid}/metadata.json"
                    archive.writestr(entry, json_bytes(message))
                    # Complete bodies, subjects and attachment metadata remain in the archive.
                    summaries.append(summary)
                    messages.append({"uid": uid, "metadata_entry": entry})
                scanned = uid
            if not blocked and len(uids) <= batch_size:
                scanned = upper
            archive.writestr("manifest.json", json_bytes({"schema_version": "1.0", "messages": messages,
                "mailbox": credentials["username"], "folder": folder, "uidvalidity": validity}))
        return {"status": "blocked" if blocked and blocked["code"] != "batch_boundary" else "ok",
            "messages": summaries, "archive": descriptor(archive_path, "imap-batch.zip", "application/zip"),
            "candidate_checkpoint": {"uidvalidity": validity, "last_committed_uid": scanned},
            "checkpoint_committed": False, "has_more": scanned < highest,
            "diagnostics": {"bytes_read": total, "scanned_through_uid": scanned,
                "matched": len(summaries), "boundary": blocked}}
    except Exception:
        if archive_path:
            archive_path.unlink(missing_ok=True)
        # Never relay server text, host, user, password or arbitrary exception messages.
        raise RuntimeError("imap_read_failed: проверьте Credentials, доступ, входы и лимиты") from None
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


@register_node_class
class IMAPReadNode:
    META = {"name": "IMAPReadNode", "display_name": "IMAP Read",
        "description": "Читает письма и вложения без изменения ящика", "package": "mail_excel",
        "version": "0.2.0", "tags": ["Mail", "IMAP"], "category": "Mail"}

    def __init__(self, **parameters):
        self.parameters = parameters

    def exec(self, **inputs):
        return read_imap(**(self.parameters | inputs))


if __name__ == "__main__":
    run_registered_node_from_cli()
