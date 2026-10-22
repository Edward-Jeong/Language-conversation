"""Product-boundary checks independent of provider mock output."""
from datetime import datetime as RealDatetime, UTC
import json
from pathlib import Path
import pytest
import apps.api.orchestrator as learning

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize(('instant','expected'),[
    ('2026-10-21T14:59:59+00:00','2026-10-21T15:00:00+00:00'),
    ('2026-10-21T15:00:00+00:00','2026-10-22T15:00:00+00:00'),
    ('2026-12-31T14:59:59+00:00','2026-12-31T15:00:00+00:00'),
])
def test_next_learning_day_is_seoul_midnight_not_utc(instant,expected,monkeypatch):
    class Clock(RealDatetime):
        @classmethod
        def now(cls,tz=None):
            value=RealDatetime.fromisoformat(instant)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(learning,'datetime',Clock)
    assert learning.LessonOrchestrator._due_at('Asia/Seoul',1)==expected


def test_content_has_required_variety_and_translations():
    catalog=learning.ScenarioCatalog()
    assert len(catalog._items)==5
    for scenario in catalog._items.values():
        assert len(scenario['opening_questions'])>=3
        assert len(scenario['intents'])>=3
        assert len(scenario['follow_up_questions'])>=5
        assert len(scenario['acceptable_alternatives'])>=2
        assert len(scenario['transfer_tasks'])>=2
        ids={i['id'] for i in scenario['intents']}
        for item in scenario['opening_questions']+scenario['follow_up_questions']+scenario['transfer_tasks']:
            assert item['intent_id'] in ids
            assert catalog.translation(item['text_en']).strip()
        for intent in ids:
            assert set(scenario['hints'][intent])=={'H1','H2','H3'}


def test_fixed_quality_set_has_independent_expected_categories():
    fixture=json.loads((ROOT/'tests/fixtures/tutor_quality.json').read_text(encoding='utf-8-sig'))
    cases=fixture['cases'];assert len(cases)==50
    assert len({c['id'] for c in cases})==50
    assert sum(c['category']=='clear_error' for c in cases)==15
    assert sum(c['category']=='normal' for c in cases)==15
    assert sum(c['category']=='acceptable_alternative' for c in cases)==10
    assert sum(c['category']=='uncertain_or_incomplete' for c in cases)==10
    assert all(c['rationale'] for c in cases)
