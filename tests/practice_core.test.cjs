const {test} = require('node:test');
const assert = require('node:assert/strict');
const {schedule, validCard} = require('../app/static/practice.js');
const now = Date.parse('2028-02-28T23:55:00Z');
test('forgot resets the streak and schedules ten minutes later across midnight', () => {
  assert.deepEqual(schedule(4, 'forgot', now), {stage: 0, due_at: '2028-02-29T00:05:00.000Z'});
});
test('partial resets the streak and schedules one day later in a leap year', () => {
  assert.deepEqual(schedule(4, 'partial', now), {stage: 0, due_at: '2028-02-29T23:55:00.000Z'});
});
test('remembering advances through the documented intervals and caps at thirty days', () => {
  [1,3,7,14,30,30].forEach((days, stage) => {
    const result = schedule(stage, 'remembered', now);
    assert.equal(Date.parse(result.due_at) - now, days * 86400000);
    assert.equal(result.stage, Math.min(stage + 1, 5));
  });
});
test('invalid records and outcomes are rejected instead of producing a misleading next date', () => {
  assert.throws(() => schedule(0, 'correct', now));
  assert.throws(() => schedule(-1, 'forgot', now));
  assert.throws(() => schedule(1, 'remembered', NaN));
});
test('cards must keep a version-bound source and evidence', () => {
  const card = {key:'material:1:r2:questions:0',title:'原文',question:'问题',answer:'答案',url:'/learning/1?revision=2#e-L1',evidence:[{text:'依据'}]};
  assert.ok(validCard(card));
  assert.equal(validCard({...card,url:'javascript:alert(1)'}), false);
  assert.equal(validCard({...card,evidence:[]}), false);
  assert.equal(validCard({...card,key:'latest-question'}), false);
});
