const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validate,merge}=require('../app/static/collection_backup.js');
const backup=()=>({format:'political-news-collection',version:1,exported_at:'2026-11-15T08:00:00Z',read:['1','1','2'],saved:{'1':{title:'原文',url:'/news/1',date:'2026-11-15'},'learning:3':{title:'资料',url:'/reader/learning/3',date:''}}});
test('collection backup keeps safe same-library links and deduplicates read records',()=>{
  assert.deepEqual(validate(backup()).read,['1','2']);
  assert.equal(Object.keys(validate(backup()).saved).length,2);
});
test('foreign formats, unsafe links and mismatched IDs cannot be restored',()=>{
  for(const url of ['//evil.test/news/1','javascript:alert(1)','/learning/2','/news/1?x=1','/news/1#x','/\\evil.test/news/1']){
    const data=backup();data.saved['1'].url=url;assert.throws(()=>validate(data));
  }
  for(const data of [{...backup(),version:2},{...backup(),read:['__proto__']},{...backup(),saved:[]},{...backup(),exported_at:'bad'}])assert.throws(()=>validate(data));
});
test('a merged news group can link to a different representative article ID',()=>{
  const data=backup();data.saved['1'].url='/news/9';assert.equal(validate(data).saved['1'].url,'/news/9');
  data.saved['learning:3'].url='/learning/9';assert.throws(()=>validate(data));
});
test('merge preserves existing same-key items, reports conflicts, and is idempotent',()=>{
  const current={read:['1'],saved:{'1':{title:'本机更正标题',url:'/news/1',date:''}}};
  const result=merge(current,backup());
  assert.equal(result.added,1);assert.equal(result.conflicts,1);assert.equal(result.read_added,1);
  assert.equal(result.state.saved['1'].title,'本机更正标题');
  assert.deepEqual(current.read,['1']);assert.equal(Object.keys(current.saved).length,1);
  const again=merge(result.state,backup());assert.equal(again.added,0);assert.equal(again.read_added,0);
});
