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
    assert run_detail("d.detailUrl('Columba palumbus','nl')") == "/api/species/bird/Columba%20palumbus?locale=nl"
    assert run_detail("d.detailUrl('A/B ?','de')") == "/api/species/bird/A%2FB%20%3F?locale=de"


def test_pose_fallbacks_cover_both_single_and_missing_assets():
    assert run_detail("d.availablePoses({perched:{url:'/p'},flight:{url:'/f'}})") == ["perched", "flight"]
    assert run_detail("d.availablePoses({perched:{url:'/p'}})") == ["perched"]
    assert run_detail("d.availablePoses({flight:{url:'/f'}})") == ["flight"]
    assert run_detail("d.availablePoses({})") == []


def test_houtduif_presentation_content_stats_audio_and_formatting():
    result = run_detail(f"d.present({json.dumps(houtduif())},'nl-NL')")
    assert result["summary"] == "Een grote duif." and result["fact"] == "Ook bosduif genoemd."
    assert result["wikipedia"]["language"] == "nl" and result["waarnemingUrl"].startswith("https://waarneming.nl/")
    assert result["total"] == 5 and result["audio"]["url"].endswith("/audio")
    assert result["confidence"] == "94,6%"
    assert "3 oktober 2026" in result["last"] and "16:28" in result["last"]
    assert run_detail("d.familyLabel('Columbidae (Duiven)')") == "Duiven \u00b7 Columbidae"


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


def test_single_shared_detail_component_is_wired_to_collage_and_atlas():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "avian-visitors.js").read_text(encoding="utf-8")
    detail = (STATIC / "avian-detail.js").read_text(encoding="utf-8")
    assert html.count('id="speciesDetail"') == 1
    assert html.index("avian-detail.js") < html.index("avian-visitors.js")
    assert 'detail.open(tile.item.scientific_name,el)' in script
    assert 'detail.open(item.scientific_name,card)' in script
    assert 'if(opener&&document.contains(opener))opener.focus()' in detail
    assert 'event.key==="Escape"' in detail and 'data-detail-close' in html


def test_avian_module_contains_no_copied_central_assets():
    forbidden = {".png", ".jpg", ".jpeg", ".webp", ".wav", ".sqlite", ".db"}
    assert not [path for path in STATIC.rglob("*") if path.suffix.lower() in forbidden]
