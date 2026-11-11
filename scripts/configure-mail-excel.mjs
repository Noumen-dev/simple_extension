// Defines the universal node contracts. No mailbox, sender or business defaults.
import fs from 'node:fs';
const rules = {requireSameType:true, requireSameDataType:false, allowMultipleOutputs:true, allowMultipleInputs:true};
const signal = key => ({type:'signal',key,connectionRules:rules});
const port = (key,dataType,description,extra={}) => ({type:'data',key,displayName:key, dataType,description,connectionRules:rules,...extra});
const parameter = (displayName,description,defaultValue) => ({displayName,description,defaultValue,control:{kind:'number'}});
const schema = (type,displayName,description,category,icon,inputs,outputs,parameters,path) => ({
  type,kind:'class',metadata:{displayName,description,icon},
  ports:{inputs:[signal('execute'),...inputs],outputs:[signal('next'),...outputs]},
  ui:{type:'default'}, parameters,
  data:{type,language:'python',package:'mail_excel',version:'0.2.0',description,tags:[category],category},
  code:{path},docs:{path:'README.md'}
});
const checkpoint = {uidvalidity:'string',last_committed_uid:'number'};
const imap = schema('IMAPReadNode','IMAP Read','Читает письма и вложения без изменения ящика','Mail','Mail',[
  port('credentials','object','Подключение к IMAPS через Credentials',{required:true,schema:{type:'object',required:['host','username','password'],properties:{
    host:{type:'string',title:'Host'},port:{type:'integer',title:'Port',minimum:1,maximum:65535,default:993},
    username:{type:'string',title:'Username'},password:{type:'string',title:'Password'}}}}),
  port('folder','string','Папка почтового ящика',{defaultValue:'INBOX'}),
  port('rules','array','Точный отправитель и подстроки темы; пустой список — все письма',{defaultValue:[],schema:{type:'array',items:{type:'object',required:['sender','subjects'],properties:{sender:{type:'string',title:'Sender'},subjects:{type:'array',title:'Subjects',items:{type:'string'}}}}}}),
  port('checkpoint','object','Последний надёжно записанный UID; пустое значение — начало истории',{schema:{type:'object',required:['uidvalidity','last_committed_uid'],properties:{uidvalidity:{type:'string',title:'UIDVALIDITY'},last_committed_uid:{type:'integer',title:'Committed UID',minimum:0}}}})
],[port('status','string','ok, blocked или recovery_required'),
   port('messages','array','Письма с метаданными, хешами и адресами частей внутри архива'),
   port('archive','file','ZIP: manifest.json, исходные MIME, текст и все вложения'),
   port('candidate_checkpoint','object','Кандидат; фиксировать только после надёжной записи всего пакета',{schema:checkpoint}),
   port('checkpoint_committed','boolean','Всегда false: нода не сохраняет checkpoint'),
   port('has_more','boolean','Остались UID для следующей страницы'),
   port('diagnostics','object','Счётчики и причина остановки')
],{
  batch_size:parameter('Batch size','Максимум проверяемых писем за вызов',25),
  max_message_bytes:parameter('Max message bytes','Максимальный размер исходного MIME',40000000),
  max_attachment_bytes:parameter('Max attachment bytes','Максимальный размер одного декодированного вложения',30000000),
  max_batch_bytes:parameter('Max batch bytes','Максимум загружаемых байтов MIME за вызов',80000000),
  max_attachments:parameter('Max attachments','Максимум вложений в одном письме',100),
  max_metadata_bytes:parameter('Max metadata bytes','Предел JSON-метаданных; оригиналы остаются в архиве',262144)
},'imap_read_node.py');
const excel = schema('ExcelReadNode','Excel Read','Извлекает исходные ячейки, формулы и объединённые диапазоны','File','Sheet',[
  port('file','file','Excel-файл или ZIP-пакет IMAP; для ZIP задайте attachment'),
  port('attachment','object','Вложение: метаданные IMAP или материализованный file-объект'),
  port('archive_entry','string','Необязательный путь вложения внутри ZIP-пакета',{defaultValue:''})
],[port('status','string','Состояние чтения'),port('cells_file','file','Полная выгрузка JSONL: листы, ячейки, формулы, объединения и предупреждения'),
   port('source','object','Имя, идентификатор и SHA256 исходного файла',{schema:{filename:'string',attachment_id:'string',sha256:'string'}}),
   port('cell_count','number','Количество извлечённых непустых ячеек'),
   port('cells','array','Ограниченный просмотр ячеек; полные данные в cells_file'),
   port('preview_truncated','boolean','Просмотр содержит не все ячейки'),
   port('warnings','array','Первые 50 предупреждений; полный список в cells_file'),
   port('warning_counts','object','Количество предупреждений каждого типа'),
   port('errors','array','Ошибки чтения'),port('schema_version','string','Версия формата JSONL'),
   port('memory_limit_enforced','boolean','Применён системный лимит адресного пространства процесса')
],{
  max_file_bytes:parameter('Max file bytes','Максимальный размер исходного Excel-файла',40000000),
  max_cells:parameter('Max cells','Максимум ячеек; для XLS также предел площади листов',1000000),
  max_uncompressed_bytes:parameter('Max expanded bytes','Предел суммарного распакованного размера OOXML',64000000),
  max_result_bytes:parameter('Max result bytes','Максимальный размер полной выгрузки JSONL',100000000),
  max_memory_bytes:parameter('Max memory bytes','Предел адресного пространства Unix-runner; на Windows действуют лимиты файлов и ячеек',512000000),
  preview_cells:parameter('Preview cells','Максимум ячеек в JSON-просмотре; также действует предел 64 КиБ',20)
},'excel_read_node.py');
for(const [folder,value] of [['imap-read',imap],['excel-read',excel]]) fs.writeFileSync(`nodes/${folder}/schema.json`,JSON.stringify(value,null,2)+'\n');
