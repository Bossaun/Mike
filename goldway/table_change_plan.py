"""Immutable, local fake-workbook change plans; no external application adapter.

Cell strings (including formulas/instructions) are inert data. The signed local
approval record binds a trusted issuer's assertion; it does not authenticate a
human or grant Google/CRM permissions. A real adapter/approval UI is out of scope.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
import hmac
import json
import re


MAX_SHEETS = 20
MAX_ROWS = 1000
MAX_CELLS = 100
MAX_CHANGES = 50
MAX_USED_NONCES = 10000
MAX_APPROVAL_SECONDS = 86400


class ConflictError(ValueError):
    pass


class LookupError(ValueError):
    pass


class ApprovalError(ValueError):
    pass


def _label(value, field):
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > 200):
        raise ValueError('Explicit bounded ' + field + ' required')
    return value


def _value(value):
    """Type tags preserve None/empty, bool/int, decimal scale and string spaces."""
    if value is None:
        return ['null', None]
    if type(value) is bool:
        return ['bool', value]
    if type(value) is int and abs(value) <= 10**18:
        return ['int', str(value)]
    if type(value) is str and len(value) <= 4096:
        return ['str', value]
    if (type(value) is Decimal and value.is_finite()
            and len(value.as_tuple().digits) <= 50 and abs(value.as_tuple().exponent) <= 18):
        return ['decimal', str(value)]
    raise ValueError('Cell value must be an explicit bounded immutable scalar; floats unsupported')


def _tuple(values, cls, minimum, maximum):
    if (type(values) is not tuple or not minimum <= len(values) <= maximum
            or any(type(value) is not cls for value in values)):
        raise ValueError('Bounded tuple of validated records required')


def _unique(values, field):
    if len(values) != len(set(values)):
        raise ValueError('Duplicate ' + field)


@dataclass(frozen=True)
class Cell:
    column: str
    value: object

    def __post_init__(self):
        _label(self.column, 'column')
        _value(self.value)


@dataclass(frozen=True)
class RowIdentity:
    column: str
    value: str

    def __post_init__(self):
        _label(self.column, 'identity column')
        # Exact identity content is preserved; leading/trailing spaces are data.
        if type(self.value) is not str or not self.value.strip() or len(self.value) > 200:
            raise ValueError('Explicit nonempty string row identity required')


@dataclass(frozen=True)
class Row:
    position: int
    revision: str
    cells: tuple[Cell, ...]

    def __post_init__(self):
        if type(self.position) is not int or not 1 <= self.position <= 10**7:
            raise ValueError('Explicit positive bounded row position required')
        _label(self.revision, 'row revision')
        _tuple(self.cells, Cell, 1, MAX_CELLS)
        _unique([cell.column for cell in self.cells], 'row column')


@dataclass(frozen=True)
class Sheet:
    id: str
    revision: str
    rows: tuple[Row, ...]

    def __post_init__(self):
        _label(self.id, 'sheet ID')
        _label(self.revision, 'sheet revision')
        _tuple(self.rows, Row, 0, MAX_ROWS)
        _unique([row.position for row in self.rows], 'physical row position')


@dataclass(frozen=True)
class Workbook:
    id: str
    revision: str
    sheets: tuple[Sheet, ...]

    def __post_init__(self):
        _label(self.id, 'workbook ID')
        _label(self.revision, 'workbook revision')
        _tuple(self.sheets, Sheet, 1, MAX_SHEETS)
        _unique([sheet.id for sheet in self.sheets], 'sheet ID')


def _cell(row, column):
    found = [cell for cell in row.cells if cell.column == column]
    if len(found) != 1:
        raise LookupError('Missing or ambiguous cell')
    return found[0]


def lookup_row(workbook: Workbook, *, workbook_id: str, sheet_id: str,
               identity: RowIdentity) -> Row:
    """Exact typed identity lookup. No fuzzy match, coercion, trim or fallback."""
    if type(workbook) is not Workbook or type(identity) is not RowIdentity:
        raise ValueError('Validated workbook and row identity required')
    _label(workbook_id, 'workbook ID')
    _label(sheet_id, 'sheet ID')
    if workbook.id != workbook_id:
        raise LookupError('Wrong workbook')
    sheets = [sheet for sheet in workbook.sheets if sheet.id == sheet_id]
    if len(sheets) != 1:
        raise LookupError('Missing or ambiguous sheet')
    rows = [row for row in sheets[0].rows if any(
        cell.column == identity.column and type(cell.value) is str and cell.value == identity.value
        for cell in row.cells)]
    if len(rows) != 1:
        raise LookupError('Missing or ambiguous row identity')
    return rows[0]


@dataclass(frozen=True)
class Change:
    column: str
    before: object
    after: object

    def __post_init__(self):
        _label(self.column, 'changed column')
        if _value(self.before) == _value(self.after):
            raise ValueError('No-op changes require no approval')


@dataclass(frozen=True)
class ChangePlan:
    workbook_id: str
    workbook_revision: str
    sheet_id: str
    sheet_revision: str
    identity: RowIdentity
    baseline: Row
    changes: tuple[Change, ...]

    def __post_init__(self):
        for name in ('workbook_id', 'workbook_revision', 'sheet_id', 'sheet_revision'):
            _label(getattr(self, name), name)
        if type(self.identity) is not RowIdentity or type(self.baseline) is not Row:
            raise ValueError('Validated row identity and baseline required')
        _tuple(self.changes, Change, 1, MAX_CHANGES)
        _unique([change.column for change in self.changes], 'changed column')
        identity_cell = _cell(self.baseline, self.identity.column)
        if type(identity_cell.value) is not str or identity_cell.value != self.identity.value:
            raise ValueError('Baseline does not contain exact row identity')
        for change in self.changes:
            if change.column == self.identity.column:
                raise ValueError('Identity mutation requires a separate migration plan')
            if _value(_cell(self.baseline, change.column).value) != _value(change.before):
                raise ValueError('Change before value differs from baseline')


def build_plan(workbook: Workbook, *, workbook_id: str, sheet_id: str,
               identity: RowIdentity, desired: tuple[Cell, ...]) -> ChangePlan:
    """Capture an entire immutable row baseline and explicit desired cells."""
    _tuple(desired, Cell, 1, MAX_CHANGES)
    _unique([cell.column for cell in desired], 'desired column')
    row = lookup_row(workbook, workbook_id=workbook_id, sheet_id=sheet_id, identity=identity)
    sheet = next(sheet for sheet in workbook.sheets if sheet.id == sheet_id)
    changes = tuple(Change(cell.column, _cell(row, cell.column).value, cell.value)
                    for cell in sorted(desired, key=lambda cell: cell.column))
    return ChangePlan(workbook.id, workbook.revision, sheet.id, sheet.revision, identity, row, changes)


def _row_data(row):
    return {'position': row.position, 'revision': row.revision,
            'cells': [[cell.column, _value(cell.value)] for cell in row.cells]}


def _plan_data(plan):
    if type(plan) is not ChangePlan:
        raise ValueError('Validated change plan required')
    return {'format': 'fake-workbook-change-plan-v1', 'workbook_id': plan.workbook_id,
            'workbook_revision': plan.workbook_revision, 'sheet_id': plan.sheet_id,
            'sheet_revision': plan.sheet_revision,
            'identity': {'column': plan.identity.column, 'value': plan.identity.value},
            'baseline': _row_data(plan.baseline),
            'changes': [{'column': change.column, 'before': _value(change.before),
                         'after': _value(change.after)}
                        for change in sorted(plan.changes, key=lambda change: change.column)]}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def plan_digest(plan: ChangePlan) -> str:
    return hashlib.sha256(_canonical(_plan_data(plan))).hexdigest()


def preview(plan: ChangePlan) -> dict:
    """Detached preview data; mutating this dict cannot mutate the plan."""
    result = _plan_data(plan)
    result['digest'] = plan_digest(plan)
    return result


@dataclass(frozen=True)
class ApprovalToken:
    plan_digest: str
    approver: str
    nonce: str
    issued_at: int
    expires_at: int
    signature: str

    def __post_init__(self):
        for value in (self.plan_digest, self.signature):
            if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
                raise ApprovalError('Malformed approval digest or signature')
        _label(self.approver, 'approver')
        _label(self.nonce, 'approval nonce')
        if (type(self.issued_at) is not int or type(self.expires_at) is not int
                or not 0 <= self.issued_at < self.expires_at <= 2**63-1
                or self.expires_at - self.issued_at > MAX_APPROVAL_SECONDS):
            raise ApprovalError('Explicit bounded approval validity required')


def _key(key):
    if type(key) is not bytes or not 32 <= len(key) <= 128:
        raise ApprovalError('Explicit local issuer signing key required')
    return key


def _approval_data(digest, approver, nonce, issued_at, expires_at):
    return {'format': 'fake-workbook-approval-v1', 'plan_digest': digest,
            'approver': approver, 'nonce': nonce, 'issued_at': issued_at,
            'expires_at': expires_at}


def issue_local_approval(plan: ChangePlan, *, decision: str, approver: str,
                         nonce: str, issued_at: int, expires_at: int,
                         signing_key: bytes) -> ApprovalToken:
    """Trusted local issuer only: caller must authenticate the approving human.

    `decision='approve'` is explicit; creation of a plan does not issue a token.
    The pure helper neither knows actual user rights nor grants external access.
    """
    if type(decision) is not str or decision != 'approve':
        raise ApprovalError('Explicit approve decision required')
    digest = plan_digest(plan)
    provisional = ApprovalToken(digest, approver, nonce, issued_at, expires_at, '0'*64)
    signature = hmac.new(_key(signing_key), _canonical(_approval_data(
        provisional.plan_digest, provisional.approver, provisional.nonce,
        provisional.issued_at, provisional.expires_at)), hashlib.sha256).hexdigest()
    return replace(provisional, signature=signature)


def validate_approval(plan: ChangePlan, token: ApprovalToken, *, required_approver: str,
                      now: int, signing_key: bytes, used_nonces: frozenset[str]) -> None:
    if type(token) is not ApprovalToken:
        raise ApprovalError('Explicit validated approval token required')
    _label(required_approver, 'required approver')
    if type(now) is not int or not 0 <= now <= 2**63-1:
        raise ApprovalError('Explicit current observation timestamp required')
    if type(used_nonces) is not frozenset or len(used_nonces) >= MAX_USED_NONCES:
        raise ApprovalError('Bounded immutable consumed-nonce ledger required')
    for nonce in used_nonces:
        _label(nonce, 'consumed nonce')
    if token.approver != required_approver or not token.issued_at <= now < token.expires_at:
        raise ApprovalError('Approval actor or validity does not match')
    if token.nonce in used_nonces:
        raise ApprovalError('Approval already consumed')
    if not hmac.compare_digest(token.plan_digest, plan_digest(plan)):
        raise ApprovalError('Approval does not match complete plan')
    expected = hmac.new(_key(signing_key), _canonical(_approval_data(
        token.plan_digest, token.approver, token.nonce, token.issued_at, token.expires_at)),
        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(token.signature, expected):
        raise ApprovalError('Invalid approval signature')


@dataclass(frozen=True)
class ApplyResult:
    workbook: Workbook
    used_nonces: frozenset[str]


def check_plan_baseline(workbook: Workbook, plan: ChangePlan) -> Row:
    """Return the unchanged target row, or reject a conflicting local snapshot.

    This read-only check grants no approval or write permission. Applying a plan
    must still validate approval before checking its baseline.
    """
    if type(plan) is not ChangePlan:
        raise ValueError('Validated change plan required')
    try:
        row = lookup_row(workbook, workbook_id=plan.workbook_id,
                         sheet_id=plan.sheet_id, identity=plan.identity)
    except LookupError:
        raise ConflictError('Target workbook/sheet/row is missing or ambiguous') from None
    sheet = next(sheet for sheet in workbook.sheets if sheet.id == plan.sheet_id)
    if (workbook.revision != plan.workbook_revision or sheet.revision != plan.sheet_revision
            or _row_data(row) != _row_data(plan.baseline)):
        raise ConflictError('Baseline changed: manual edit, row move or revision conflict')
    return row


def apply_to_fake_workbook(workbook: Workbook, plan: ChangePlan, token: ApprovalToken, *,
                          required_approver: str, now: int, signing_key: bytes,
                          used_nonces: frozenset[str], next_workbook_revision: str,
                          next_sheet_revision: str, next_row_revision: str) -> ApplyResult:
    """Return a new fake snapshot or fail closed; never write an external sheet.

    Caller must atomically retain BOTH returned workbook and consumed ledger.
    This in-memory function cannot prevent replay if a caller discards its state.
    """
    validate_approval(plan, token, required_approver=required_approver, now=now,
                      signing_key=signing_key, used_nonces=used_nonces)
    row = check_plan_baseline(workbook, plan)
    sheet = next(sheet for sheet in workbook.sheets if sheet.id == plan.sheet_id)
    for current, proposed in ((workbook.revision, next_workbook_revision),
                              (sheet.revision, next_sheet_revision), (row.revision, next_row_revision)):
        _label(proposed, 'new revision')
        if current == proposed:
            raise ConflictError('Applied changes require new explicit revisions')
    after = {change.column: change.after for change in plan.changes}
    changed_row = replace(row, revision=next_row_revision,
                          cells=tuple(Cell(cell.column, after.get(cell.column, cell.value)) for cell in row.cells))
    changed_sheet = replace(sheet, revision=next_sheet_revision,
                            rows=tuple(changed_row if candidate.position == row.position else candidate
                                       for candidate in sheet.rows))
    changed_book = replace(workbook, revision=next_workbook_revision,
                           sheets=tuple(changed_sheet if candidate.id == sheet.id else candidate
                                        for candidate in workbook.sheets))
    return ApplyResult(changed_book, used_nonces | {token.nonce})
