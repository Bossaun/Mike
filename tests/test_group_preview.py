from dataclasses import fields
import itertools
import pytest
from goldway.group_preview import ReviewedText, Visibility, SummaryItem, preview


def text(value='Макет', visibility=Visibility.ROUTINE):
    return ReviewedText(value, visibility)


def item(**kwargs):
    return SummaryItem(title=text(), status='confirmed', **kwargs)


def test_only_allowlisted_reviewed_fields_in_preview():
    result = preview([item(owner=text('Сотрудник'), deadline=text('Пятница'),
                           next_step=text('Подготовить макет'))])
    assert 'Сотрудник' in result.text and 'Подготовить макет' in result.text
    assert result.included_items == 1 and result.omitted_fields == 0
    assert result.requires_review and not result.live_send_authorized
    assert {f.name for f in fields(SummaryItem)} == {'title','status','owner','deadline','next_step'}


@pytest.mark.parametrize('visibility', [Visibility.FINANCIAL, Visibility.PRIVATE, Visibility.UNKNOWN])
def test_sensitive_title_discards_entire_record(visibility):
    hidden = SummaryItem(text('SECRET_TITLE', visibility), 'confirmed', owner=text('SECRET_OWNER'))
    result = preview([hidden])
    assert 'SECRET' not in result.text and result.omitted_items == 1
    assert result.included_items == 0


@pytest.mark.parametrize('name', ['owner','deadline','next_step'])
@pytest.mark.parametrize('visibility', [Visibility.FINANCIAL, Visibility.PRIVATE, Visibility.UNKNOWN])
def test_sensitive_optional_field_never_copied(name, visibility):
    result = preview([item(**{name: text('SECRET_PAYLOAD', visibility)})])
    assert 'SECRET_PAYLOAD' not in result.text
    assert result.included_items == 1 and result.omitted_fields == 1


def test_limit_does_not_expose_overflow_items():
    result = preview([item(), SummaryItem(text('OVERFLOW'), 'confirmed')], max_items=1)
    assert 'OVERFLOW' not in result.text and result.omitted_items == 1


def test_unbounded_iterable_consumption_is_bounded():
    with pytest.raises(ValueError, match='too_many_items'):
        preview(itertools.repeat(item()))


@pytest.mark.parametrize('bad', [True, 0, 21, '2'])
def test_invalid_limit(bad):
    with pytest.raises(ValueError): preview([], max_items=bad)


@pytest.mark.parametrize('bad', ['', 'a'*301, 'x\ny', 'x\ry', 'x\ty', 'x\x7fy'])
def test_invalid_field_text(bad):
    with pytest.raises(ValueError): text(bad)


def test_raw_dicts_not_accepted_and_no_transport_fields():
    with pytest.raises(ValueError): preview([{'title':'secret','token':'secret'}])
    with pytest.raises(TypeError): SummaryItem(text(), 'confirmed', source='private')
    with pytest.raises(ValueError): SummaryItem(text(), 'unknown')
    with pytest.raises(ValueError): item(owner='unreviewed')
    with pytest.raises(ValueError): ReviewedText('test', 'routine')


def test_input_immutable_and_status_kept():
    source = SummaryItem(text(), 'completed')
    result = preview([source])
    assert source.status == 'completed' and 'Выполнено' in result.text
    assert 'отдельное разрешение' in result.text
