from app.news_body import extract_body
from app.fetch_news import _parse_article_detail,_fetch_url,save_news_to_db
from app.database import SessionLocal
from app.models import AppState,News
from tests.test_events import row
from unittest.mock import patch,MagicMock


def test_long_article_keeps_all_paragraphs_and_short_quotes_inside_form():
    parts=['正文段落'+str(i)+'，政策措施和具体条件需要保留原文，不能因为超过1200字而提前停止。'*4 for i in range(20)]
    parts.insert(5,'“不会。”')
    html='<nav><p>导航内容不能混入正文</p></nav><form><div id="articleText">'+''.join('<p>'+p+'</p>' for p in parts)+'</div></form>'
    summary,body,meta=extract_body(html)
    assert body=='\n'.join(parts) and meta['paragraphs']==21
    assert len(body)>1200 and summary==parts[0]
    assert _parse_article_detail(html)[1]==body


def test_unsupported_page_verification_and_pagination_are_not_complete():
    assert extract_body('<p>访问异常，请完成安全验证后重试访问原文内容。</p>')[2]['status']=='blocked'
    assert extract_body('<p>这是一段没有可信正文容器的导航文字，需要保持未核实状态而不是当作完整文章。</p>')[2]['status']=='unverified'
    html='<div class="rm_txt_con"><p>这是一段足够长度的文章正文，具体事实仍应回到原文进行核对，正文还有后续页面。</p><a href="next.html">下一页</a></div>'
    assert extract_body(html)[2]['status']=='partial'


def test_legacy_gb2312_page_decodes_without_silently_dropping_chinese():
    text='<meta charset="gb2312"><div class="pages_content"><p>国务院发布公共服务条例，明确职责和实施安排。</p></div>'
    response=MagicMock();response.__enter__.return_value=response;response.read.return_value=text.encode('gb2312')
    with patch('app.fetch_news.urlopen',return_value=response):assert _fetch_url('https://www.gov.cn/test')==text


def test_failed_recapture_preserves_body_and_capture_state():
    original=row();item={k:getattr(original,k) for k in ['title','summary','content','source','category','link','published','published_at','year','month']}
    item['body_capture']={'status':'extracted','method':'body-extract-2','paragraphs':1}
    save_news_to_db([item])
    with SessionLocal() as db:
        identity=db.query(News).one().id;old=db.query(News).one().content
    save_news_to_db([{**item,'content':'验证码','summary':'验证码','body_capture':{'status':'blocked','method':'body-extract-2'}}])
    import json
    with SessionLocal() as db:
        assert db.get(News,identity).content==old
        meta=json.loads(db.get(AppState,'news_body:'+str(identity)).value)
        assert meta['status']=='extracted' and meta['last_attempt_status']=='blocked'
