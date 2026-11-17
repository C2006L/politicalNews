from app.material_readout import readout


def test_policy_keeps_conditions_units_and_negation():
    line='个人消费者在省内购买符合条件的产品，补贴比例为15%，每件最高不超过1500元，资金用完即止。'
    material={'title':'消费品补贴范围再扩容','account':'山东发布','url':'https://mp.weixin.qq.com/s/test','evidence':[{'id':'L1','text':line}]}
    result=readout(material)
    assert len(result)==1 and result[0]['quote']==line and result[0]['evidence_id']=='L1'


def test_teacher_drills_and_advice_never_turn_into_policy_readouts():
    for title in ['腿姐带背D28 复习','同一份申论材料，国考和省考要的答案不一样']:
        assert not readout({'title':title,'account':'老师','url':'https://mp.weixin.qq.com/s/test','evidence':[{'id':'L1','text':'应当注意条件与表述，原文列出了十五个不同主题的背诵方法与答题建议。'}]})