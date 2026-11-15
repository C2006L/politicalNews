const {test}=require('node:test');const assert=require('node:assert/strict');
const {calendar}=require('../app/static/study_live.js');
test('Beijing midnight changes the calendar at the exact second',()=>{
 assert.equal(calendar(Date.parse('2026-11-15T15:59:59Z')).key,'2026-11-15');
 assert.equal(calendar(Date.parse('2026-11-15T16:00:00Z')).key,'2026-11-16');
 assert.equal(calendar(Date.parse('2026-11-15T16:00:00Z')).weekday,'星期一');
});
test('year end, leap day, and backwards system-clock changes are derived from current time',()=>{
 assert.equal(calendar(Date.parse('2026-12-31T16:00:00Z')).key,'2027-01-01');
 assert.equal(calendar(Date.parse('2028-02-28T16:00:00Z')).key,'2028-02-29');
 assert.equal(calendar(Date.parse('2026-11-14T16:00:00Z')).key,'2026-11-15');
});
