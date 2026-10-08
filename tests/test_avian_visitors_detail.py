import json
import subprocess
from pathlib import Path


STATIC = Path(__file__).parents[1] / "app" / "modules" / "avian_visitors" / "static"


def run_detail(expression):
    script = f"const d=require({json.dumps(str(STATIC / 'avian-detail.js'))});console.log(JSON.stringify({expression}));"
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def houtduif(**updates):
    data = {
        "identity": {"common_name": "Houtduif", "scientific_name": "Columba palumbus", "family": "Columbidae (Duiven)"},
        "waarneming": {"status": "inheems", "rarity": "algemeen", "url": "https://waarneming.nl/species/123/"},
        "observations": {"total": 5, "today": 5, "last_7_days": 5, "highest_confidence": .946,
                         "first_observed_at": "2026-10-01T12:00:00Z", "last_observed_at": "2026-10-03T14:28:00Z"},
        "audio": {"availability": True, "url": "/api/observations/x/audio", "timestamp": "2026-10-03T14:28:00Z", "confidence": .946},
        "generator": {"status": "ready", "assets": {"perched": {"url": "/p"}, "flight": {"url": "/f"}}},
        "encyclopedia": {"summary_nl": "Een grote duif.", "fact_nl": "Ook bosduif genoemd.",
                         "wikipedia_nl_url": "https://nl.wikipedia.org/wiki/Houtduif", "wikipedia_en_url": "https://en.wikipedia.org/wiki/Common_wood_pigeon"},
    }
    data.update(updates)
    return data


def test_species_endpoint_and_scientific_name_are_encoded():
    assert run_detail("d.detailUrl('Columba palumbus','nl')") == "/api/avian-visitors/detail/Columba%20palumbus?locale=nl"
    assert run_detail("d.detailUrl('A/B ?','de','otje')") == "/api/avian-visitors/detail/A%2FB%20%3F?locale=de&identity=otje"


def test_pose_fallbacks_cover_both_single_and_missing_assets():
    assert run_detail("d.availablePoses({perched:{url:'/p'},flight:{url:'/f'}})") == ["perched", "flight"]
    assert run_detail("d.availablePoses({perched:{url:'/p'}})") == ["perched"]
    assert run_detail("d.availablePoses({flight:{url:'/f'}})") == ["flight"]
    assert run_detail("d.availablePoses({})") == []


def test_pose_toggle_uses_original_icons_with_accessible_labels_and_left_alignment():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "avian-visitors.css").read_text(encoding="utf-8")
    assert 'data-pose="perched" aria-label="Zittend"><svg viewBox="0 0 640 512"' in html
    assert 'data-pose="flight" aria-label="Vliegend"><svg viewBox="0 0 512 512"' in html
    assert '>Zittend</button>' not in html and '>Vliegend</button>' not in html
    assert '.postcard-pose-toggle{position:static;left:auto;bottom:auto;transform:none;justify-self:start}' in css
    assert '.postcard-pose-toggle button svg{width:13px;height:13px;display:block' in css
    assert '.postcard-pose-toggle button[aria-current="true"] svg{opacity:.72}' in css


def test_explicit_loading_loaded_error_and_idle_visibility():
    assert run_detail("d.stateVisibility('idle')") == {"loading": False, "content": False, "error": False}
    assert run_detail("d.stateVisibility('loading')") == {"loading": True, "content": False, "error": False}
    assert run_detail("d.stateVisibility('loaded')") == {"loading": False, "content": True, "error": False}
    assert run_detail("d.stateVisibility('error')") == {"loading": False, "content": False, "error": True}


def test_houtduif_presentation_content_stats_audio_and_formatting():
    result = run_detail(f"d.present({json.dumps(houtduif())},'nl-NL')")
    assert result["summary"] == "Een grote duif." and result["fact"] == "Ook bosduif genoemd."
    assert result["wikipedia"]["language"] == "nl" and result["waarnemingUrl"].startswith("https://waarneming.nl/")
    assert result["total"] == 5 and result["audio"]["url"].endswith("/audio")
    assert result["confidence"] == "94,6%"
    assert "3 oktober 2026" in result["last"] and "16:28" in result["last"]
    assert run_detail("d.familyLabel('Columbidae (Duiven)')") == "Duiven \u00b7 Columbidae"


def test_local_identity_content_changes_copy_but_not_species_links_or_evidence():
    data = houtduif(
        presentation={"id": "otje", "display_name": "Otje", "subtitle": "Barnevelder"},
        local_content={"summary_nl": "Lokale samenvatting.", "fact_nl": "Lokaal feit."},
    )
    result = run_detail(f"d.present({json.dumps(data)},'nl-NL')")
    assert result["summary"] == "Lokale samenvatting." and result["fact"] == "Lokaal feit."
    assert result["wikipedia"]["url"] == data["encyclopedia"]["wikipedia_nl_url"]
    assert result["waarnemingUrl"] == data["waarneming"]["url"]
    assert result["audio"] == data["audio"] and result["total"] == 5


def test_story_slider_two_single_and_empty_states_and_navigation():
    assert run_detail("d.storySlides('Samenvatting','Feit')") == [
        {"title": "Over deze vogel", "text": "Samenvatting"},
        {"title": "Wist je dat?", "text": "Feit"},
    ]
    assert run_detail("d.storySlides('Alleen summary',null)") == [{"title": "Over deze vogel", "text": "Alleen summary"}]
    assert run_detail("d.storySlides(null,'Alleen feit')") == [{"title": "Wist je dat?", "text": "Alleen feit"}]
    assert run_detail("d.storySlides(null,null)") == []
    assert run_detail("[d.steppedIndex(0,1,2),d.steppedIndex(0,-1,2),d.steppedIndex(1,1,2)]") == [1, 1, 0]


def test_optional_fact_wikipedia_audio_and_zero_count_fallbacks():
    data = houtduif(
        observations={"total": 0, "highest_confidence": None},
        audio=None,
        encyclopedia={"summary_nl": "Kort.", "fact_nl": None, "wikipedia_nl_url": None,
                      "wikipedia_en_url": "https://en.wikipedia.org/wiki/Common_wood_pigeon"},
    )
    result = run_detail(f"d.present({json.dumps(data)},'nl-NL')")
    assert result["total"] == 0 and result["fact"] is None and result["audio"] is None
    assert result["confidence"] is None and result["wikipedia"]["language"] == "en"
    data["encyclopedia"]["wikipedia_en_url"] = None
    assert run_detail(f"d.present({json.dumps(data)},'nl-NL').wikipedia") is None


def test_null_encyclopedia_and_waarneming_keep_observations_audio_and_assets():
    data = houtduif(waarneming=None, encyclopedia=None)
    result = run_detail(f"d.present({json.dumps(data)},'nl-NL')")
    assert result["summary"] is None and result["fact"] is None
    assert result["wikipedia"] is None and result["waarnemingUrl"] is None
    assert result["total"] == 5 and result["audio"]["url"].endswith("/audio")
    assert result["poses"] == ["perched", "flight"]


def test_stale_species_response_is_rejected_by_request_guard():
    assert run_detail("d.isCurrentRequest(2,2)") is True
    assert run_detail("d.isCurrentRequest(1,2)") is False


def test_single_shared_detail_component_is_wired_to_collage_and_atlas():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "avian-visitors.js").read_text(encoding="utf-8")
    detail = (STATIC / "avian-detail.js").read_text(encoding="utf-8")
    assert html.count('id="speciesDetail"') == 1
    assert html.index("avian-detail.js") < html.index("avian-visitors.js")
    assert 'detail.open(tile.item.scientific_name,el,tile.item.identity_id)' in script
    assert 'detail.open(item.scientific_name,card,item.identity_id)' in script
    assert 'if(opener&&document.contains(opener))opener.focus()' in detail
    assert 'event.key==="Escape"' in detail and 'data-detail-close' in html


def test_dom_state_contract_hides_loading_and_old_content_on_success_or_error():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "avian-visitors.css").read_text(encoding="utf-8")
    detail = (STATIC / "avian-detail.js").read_text(encoding="utf-8")
    assert 'id="detailLoading"' in html and 'id="detailError"' in html
    assert '.postcard-modal [hidden]{display:none!important}' in css
    assert '.postcard-modal.is-loading .postcard-visual' in css
    assert '.postcard-modal.is-error .postcard-visual' in css
    assert 'setState("loading")' in detail
    assert 'setState("loaded")' in detail
    assert 'setState("error",error&&error.message)' in detail
    assert 'body.hidden=!visibility.content' in detail
    assert 'errorBox.hidden=!visibility.error' in detail
    assert 'controller.abort()' in detail


def test_story_slider_keeps_original_observation_audio_and_link_presentation():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "avian-visitors.css").read_text(encoding="utf-8")
    detail = (STATIC / "avian-detail.js").read_text(encoding="utf-8")
    assert 'id="detailStory"' in html and 'data-story-step="-1"' in html and 'data-story-step="1"' in html
    assert '.postcard-garden dl{margin:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr))' in css
    assert '.postcard-audio{display:flex;align-items:center;gap:13px;flex-wrap:wrap}' in css
    assert '.postcard-links{display:flex;flex-wrap:wrap;gap:9px;padding-top:3px}' in css
    assert 'obs.today==null?null' in detail and 'obs.last_7_days==null?null' in detail
    assert '"Afgelopen 7 dagen"' in detail and '"Eerste waarneming",vm.first' in detail
    assert 'formatDateTime(vm.audio.timestamp' in detail
    assert '"Meer op Wikipedia"' in detail and '"Bekijk op Waarneming.nl"' in detail
    assert 'event.key==="ArrowLeft"||event.key==="ArrowRight"' in detail
    assert 'touchstart' in detail and 'touchend' in detail
    story_rule = css.split('.postcard-story{', 1)[1].split('}', 1)[0]
    assert 'background:' not in story_rule


def test_postcard_desktop_and_mobile_layout_contracts_avoid_internal_clipping():
    css = (STATIC / "avian-visitors.css").read_text(encoding="utf-8")
    assert 'max-height:min(760px,calc(100dvh - 48px))' in css
    assert '.postcard-content{padding:30px clamp(32px,3vw,44px)}' in css
    assert '.postcard-content>div:not([hidden]){gap:16px}' in css
    assert '@media(max-width:720px){.postcard-sheet{grid-template-columns:1fr;grid-template-rows:minmax(220px,34dvh) auto}' in css
    assert '.postcard-story{height:145px;min-height:145px' in css


def test_collage_gets_a_taller_canvas_and_expands_successful_small_packs():
    css = (STATIC / "avian-visitors.css").read_text(encoding="utf-8")
    script = (STATIC / "avian-visitors.js").read_text(encoding="utf-8")
    assert 'stage.classList.toggle("is-collage",state.view===0)' in script
    assert '@media(min-width:901px)' in css
    assert '.stage.is-collage #v0{padding:154px 32px 0}' in css
    assert '.stage.is-collage .static-head{position:absolute;top:0;left:0;right:0;padding:84px 32px 22px' in css
    assert '.stage.is-collage .static-head .pre{opacity:1;max-height:30px;margin:0 0 6px;transform:none}' in css
    assert '.stage.is-collage .static-head small{opacity:1;max-height:30px;margin-top:7px;transform:none}' in css
    assert '.stage.is-collage .static-head h1{font-size:clamp(24px,3.2vw,40px);letter-spacing:.06em;transform:none}' in css
    assert '@media(min-width:701px) and (max-width:900px)' in css
    assert 'function maskPack(' in script and 'countExp:.65' in script
    assert 'postScaleMax:n<=4?1.75:n<=12?1.60:n<=25?1.42:1' in script
    assert 'if(!placed.some(function(t){return t.x<-1000;}))b=expandPacked(' in script


def test_collage_label_typography_uses_late_editorial_serif_override():
    css = (STATIC / "avian-visitors.css").read_text(encoding="utf-8")
    assert '--avian-display:ui-serif,"Iowan Old Style","Palatino Linotype","Book Antiqua",Georgia,serif' in css
    assert css.rfind('.gtile-label text{fill:var(--ink-2)') > css.find('"Segoe Print"')
    assert 'stroke-width:.72px' in css and 'letter-spacing:.025em' in css


def test_avian_module_contains_no_copied_central_assets():
    forbidden = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".sqlite", ".db"}
    assert not [path for path in STATIC.rglob("*") if path.suffix.lower() in forbidden]
