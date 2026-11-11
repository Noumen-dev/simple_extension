import fs from 'node:fs';
import assert from 'node:assert/strict';
const index = JSON.parse(fs.readFileSync('index.json'));
assert.deepEqual(index, ['imap-read','excel-read']);
for(const folder of index) {
  const s = JSON.parse(fs.readFileSync(`nodes/${folder}/schema.json`));
  for(const key of ['type','kind','metadata','ports','ui','data','code']) assert.ok(s[key],`${folder}: ${key}`);
  assert.equal(s.data.type,s.type);
  assert.equal(s.kind,'class');
  assert.ok(s.metadata.description);
  assert.ok(fs.existsSync(`nodes/${folder}/${s.code.path}`));
  for(const port of [...s.ports.inputs,...s.ports.outputs]) assert.deepEqual(port.connectionRules,{
    requireSameType:true,requireSameDataType:false,allowMultipleOutputs:true,allowMultipleInputs:true
  });
  const py = fs.readFileSync(`nodes/${folder}/${s.code.path}`,'utf8');
  assert.ok(py.includes(`class ${s.type}:`));
  assert.ok(py.includes('@register_node_class'));
  assert.ok(py.includes('run_registered_node_from_cli()'));
  assert.ok(!/efko|КТЧ|кетчуп|ketchup/i.test(py));
}
console.log('2 node contracts validated');
