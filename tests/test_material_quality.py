from app.material_quality import inspect


def test_media_partial_blocked_and_readable_are_distinct():
    payload={'evidence':[{'id':'L1','text':'明确实施政策，保留适用对象和条件。'*15}]}
    assert inspect(payload,'<div id="js_content"><p>正文</p></div>')['featured_eligible']
    assert inspect({'evidence':[]},'<div id="js_content"><img><img></div>')['status']=='image_primary'
    assert inspect(payload,'<div id="js_content"><a href="page2">下一页</a></div>')['status']=='partial'
    assert inspect(payload,'<p>环境异常，请完成验证</p>')['status']=='blocked'
    assert not inspect(payload,None)['featured_eligible']


def test_quality_page_reports_legacy_uncertainty_without_faking_full_text(client):
    from tests.test_material_navigation import seed
    seed()
    assert '正文范围待核对' in client.get('/material-quality').text
    assert not client.get('/api/learning/2').json()['quality']['featured_eligible']
