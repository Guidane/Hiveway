"""Field types: how each kind of column checks, stores and shows its values.

Values of stored types live in ``Row.data[field.key]``. Links live in the
Link table and images in Attachment, so those two types are not stored.

To add a type: subclass FieldType, decorate it with @register, teach the row
form (templates/builder/widgets.html) to edit it, and document it in AI_GUIDE.md.
"""
import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator

REGISTRY = {}


def register(cls):
    REGISTRY[cls.key] = cls()
    return cls


def get(type_key):
    return REGISTRY.get(type_key) or REGISTRY['text']


def choices():
    return [(key, kind.label) for key, kind in REGISTRY.items()]


def _text(raw):
    if raw is None:
        return ''
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    return str(raw).strip()


def _bool(raw):
    if isinstance(raw, str):
        return raw.strip().lower() in ('1', 'on', 'true', 'yes', 'y')
    return bool(raw)


TEXT_OPS = [('contains', 'contains'), ('equals', 'is'), ('not_equals', 'is not'),
            ('empty', 'is empty'), ('not_empty', 'is not empty')]
NUMBER_OPS = [('eq', '='), ('ne', '≠'), ('lt', '<'), ('lte', '≤'), ('gt', '>'), ('gte', '≥'),
              ('empty', 'is empty'), ('not_empty', 'is not empty')]
DATE_OPS = [('on', 'is on'), ('before', 'is before'), ('after', 'is after'),
            ('empty', 'is empty'), ('not_empty', 'is not empty')]
SELECT_OPS = [('equals', 'is'), ('not_equals', 'is not'), ('empty', 'is empty'), ('not_empty', 'is not empty')]
CHECKBOX_OPS = [('true', 'is ticked'), ('false', 'is not ticked')]
LINK_OPS = [('is', 'is'), ('empty', 'is empty'), ('not_empty', 'is not empty')]


class FieldType:
    key = ''
    label = ''
    description = ''
    stored = True          # value kept in Row.data
    can_unique = False
    can_required = True
    sortable = True
    inline_edit = True     # editable in a grid cell
    filter_ops = []

    def clean_options(self, options, table):
        """Check a field's options; return them normalised or raise ValidationError."""
        return {}

    def default(self, field):
        return None

    def clean(self, field, raw):
        """Turn a form or JSON value into what is stored; raise ValidationError."""
        return raw

    def is_empty(self, value):
        return value is None or value == '' or value == [] or value == {}

    def display(self, field, value):
        return '' if self.is_empty(value) else str(value)

    def search_text(self, field, value):
        return self.display(field, value)


def _default_option(options, cleaned, kind, field=None):
    if options.get('default') not in (None, ''):
        cleaned['default'] = kind.clean(field, options['default'])
    return cleaned


@register
class Text(FieldType):
    key = 'text'
    label = 'Text'
    description = 'One line of text.'
    can_unique = True
    filter_ops = TEXT_OPS
    max_length = 500

    def clean_options(self, options, table):
        return _default_option(options, {}, self)

    def default(self, field):
        return field.options.get('default', '')

    def clean(self, field, raw):
        value = ' '.join(_text(raw).split())
        if len(value) > self.max_length:
            raise ValidationError(f'At most {self.max_length} characters.')
        return value


@register
class LongText(Text):
    key = 'long_text'
    label = 'Long text'
    description = 'Several lines, e.g. notes.'
    can_unique = False
    max_length = 20000

    def clean(self, field, raw):
        value = _text(raw).replace('\r\n', '\n')
        if len(value) > self.max_length:
            raise ValidationError(f'At most {self.max_length} characters.')
        return value


@register
class Number(FieldType):
    key = 'number'
    label = 'Number'
    description = 'A number; options.decimals fixes the decimal places.'
    can_unique = True
    filter_ops = NUMBER_OPS

    def clean_options(self, options, table):
        cleaned = {}
        decimals = options.get('decimals')
        if decimals not in (None, ''):
            try:
                decimals = int(decimals)
            except (TypeError, ValueError):
                raise ValidationError('decimals must be a whole number from 0 to 10.')
            if not 0 <= decimals <= 10:
                raise ValidationError('decimals must be a whole number from 0 to 10.')
            cleaned['decimals'] = decimals
        if options.get('default') not in (None, ''):
            cleaned['default'] = self.clean(None, options['default'], decimals=cleaned.get('decimals'))
        return cleaned

    def default(self, field):
        return field.options.get('default')

    def clean(self, field, raw, decimals=None):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        if isinstance(raw, bool):
            raise ValidationError('Enter a number.')
        try:
            number = Decimal(str(raw).strip().replace(' ', ''))
        except InvalidOperation:
            raise ValidationError('Enter a number.')
        if not number.is_finite():
            raise ValidationError('Enter a number.')
        if field is not None:
            decimals = field.options.get('decimals')
        if decimals is not None:
            number = round(number, decimals)
            return int(number) if decimals == 0 else float(number)
        return int(number) if number == number.to_integral_value() else float(number)

    def display(self, field, value):
        if self.is_empty(value):
            return ''
        decimals = field.options.get('decimals') if field else None
        if decimals is not None:
            return f'{value:.{decimals}f}'
        return format(value, '.12g') if isinstance(value, float) else str(value)


@register
class Checkbox(FieldType):
    key = 'checkbox'
    label = 'Checkbox'
    description = 'Yes or no; options.default is true or false.'
    can_required = False
    filter_ops = CHECKBOX_OPS

    def clean_options(self, options, table):
        return {'default': True} if _bool(options.get('default', False)) else {}

    def default(self, field):
        return bool(field.options.get('default', False))

    def clean(self, field, raw):
        return _bool(raw)

    def is_empty(self, value):
        return value is None

    def display(self, field, value):
        return '✓' if value else ''

    def search_text(self, field, value):
        return ''


@register
class Date(FieldType):
    key = 'date'
    label = 'Date'
    description = 'A calendar date, stored as YYYY-MM-DD.'
    filter_ops = DATE_OPS

    def clean(self, field, raw):
        value = _text(raw)
        if not value:
            return None
        try:
            return datetime.date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            raise ValidationError('Enter a date as YYYY-MM-DD.')


@register
class Select(FieldType):
    key = 'select'
    label = 'Single select'
    description = 'One of a fixed list: options.choices.'
    filter_ops = SELECT_OPS

    def clean_options(self, options, table):
        raw = options.get('choices') or []
        if isinstance(raw, str):
            raw = raw.splitlines()
        if not isinstance(raw, list):
            raise ValidationError('choices must be a list of texts.')
        seen, out = set(), []
        for choice in raw:
            choice = ' '.join(_text(choice).split())[:100]
            if choice and choice.lower() not in seen:
                seen.add(choice.lower())
                out.append(choice)
        if not out:
            raise ValidationError('A select needs at least one choice.')
        cleaned = {'choices': out}
        default = _text(options.get('default'))
        if default:
            if default not in out:
                raise ValidationError(f'The default "{default}" is not one of the choices.')
            cleaned['default'] = default
        return cleaned

    def default(self, field):
        return field.options.get('default', '')

    def clean(self, field, raw):
        value = _text(raw)
        if value and value not in field.options.get('choices', []):
            match = [c for c in field.options.get('choices', []) if c.lower() == value.lower()]
            if not match:
                raise ValidationError(f'"{value}" is not one of the choices.')
            value = match[0]
        return value


@register
class Url(Text):
    key = 'url'
    label = 'Link to a web page'
    description = 'A web address, e.g. a datasheet.'
    can_unique = False
    max_length = 2000

    def clean(self, field, raw):
        value = _text(raw)
        if not value:
            return ''
        if '://' not in value:
            value = 'https://' + value
        URLValidator(message='Enter a valid web address.')(value)
        return value


@register
class Email(Text):
    key = 'email'
    label = 'Email'
    description = 'An email address.'
    max_length = 254

    def clean(self, field, raw):
        value = _text(raw)
        if value:
            EmailValidator(message='Enter a valid email address.')(value)
        return value


@register
class LinkType(FieldType):
    key = 'link'
    label = 'Link to rows'
    description = ('Rows of another (or the same) table. options.target = "app.table"; '
                   'multiple (default true); symmetric = two-way between rows of the same table; '
                   'related_name = heading of the list shown on the linked row.')
    stored = False
    sortable = False
    inline_edit = False
    filter_ops = LINK_OPS

    def clean_options(self, options, table):
        from .records import resolve_table
        ref = _text(options.get('target'))
        if not ref:
            raise ValidationError('A link needs options.target, e.g. "parts.categories".')
        target = resolve_table(ref)
        if target is None:
            raise ValidationError(f'The target table "{ref}" does not exist.')
        multiple = _bool(options.get('multiple', True))
        symmetric = _bool(options.get('symmetric', False))
        if symmetric and (target.pk != table.pk or not multiple):
            raise ValidationError('Only a multiple link to its own table can be symmetric (two-way).')
        cleaned = {'target': target.ref, 'multiple': multiple}
        if symmetric:
            cleaned['symmetric'] = True
        related = ' '.join(_text(options.get('related_name')).split())[:100]
        if related:
            cleaned['related_name'] = related
        return cleaned

    def clean(self, field, raw):
        if raw in (None, '', [], 0, '0'):
            return []
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        ids = []
        for item in items:
            if isinstance(item, dict):
                item = item.get('id')
            try:
                pk = int(item)
            except (TypeError, ValueError):
                raise ValidationError(f'"{item}" is not a row id.')
            if pk not in ids:
                ids.append(pk)
        if len(ids) > 1 and not field.options.get('multiple', True):
            raise ValidationError('Pick only one.')
        return ids


@register
class Images(FieldType):
    key = 'images'
    label = 'Images'
    description = 'Pictures, added on the row page. The first one is the cover.'
    stored = False
    sortable = False
    inline_edit = False
    can_required = False


@register
class AttributeDefs(FieldType):
    key = 'attribute_defs'
    label = 'Attribute list'
    description = ('A list of attribute names (each with a hint and whether it is required), '
                   'e.g. on a category: the attributes its parts have.')
    sortable = False
    inline_edit = False
    can_required = False

    def clean(self, field, raw):
        if raw in (None, ''):
            return []
        if not isinstance(raw, list):
            raise ValidationError('Expected a list of attributes.')
        out, seen = [], set()
        for item in raw:
            if isinstance(item, str):
                item = {'name': item}
            if not isinstance(item, dict):
                raise ValidationError('Each attribute is {"name": ..., "hint": ..., "required": true/false}.')
            name = ' '.join(_text(item.get('name')).split())[:100]
            if not name:
                continue
            if name.lower() in seen:
                raise ValidationError(f'"{name}" is listed twice.')
            seen.add(name.lower())
            entry = {'name': name}
            hint = ' '.join(_text(item.get('hint')).split())[:200]
            if hint:
                entry['hint'] = hint
            if _bool(item.get('required', False)):
                entry['required'] = True
            out.append(entry)
        return out

    def default(self, field):
        return []

    def display(self, field, value):
        return ', '.join(d['name'] + ('*' if d.get('required') else '') for d in value or [])


@register
class AttributeValues(FieldType):
    key = 'attribute_values'
    label = 'Attribute values'
    description = ('Values for the attributes listed on the linked row, e.g. a part fills in its category\'s '
                   'attributes. options.via = key of a single link field of this table; options.defs = key of '
                   'the attribute list field on the linked table. Nested rows also get their parents\' attributes.')
    sortable = False
    inline_edit = False
    can_required = False

    def clean_options(self, options, table):
        from .records import resolve_table
        via_key, defs_key = _text(options.get('via')), _text(options.get('defs'))
        via = table.fields.filter(key=via_key).first()
        if via is None or via.type != 'link':
            raise ValidationError(f'options.via must be the key of a link field of this table (got "{via_key}").')
        if via.options.get('multiple', True):
            raise ValidationError(f'The link "{via_key}" must allow only one row (multiple: false).')
        target = resolve_table(via.options.get('target'))
        defs = target.fields.filter(key=defs_key).first() if target else None
        if defs is None or defs.type != 'attribute_defs':
            raise ValidationError(f'options.defs must be the key of an attribute list field of {via.options.get("target")} '
                                  f'(got "{defs_key}").')
        return {'via': via_key, 'defs': defs_key}

    def clean(self, field, raw):
        if raw in (None, ''):
            return {}
        if not isinstance(raw, dict):
            raise ValidationError('Expected {"attribute name": "value", ...}.')
        out = {}
        for name, value in raw.items():
            value = ' '.join(_text(value).split())[:500]
            if value:
                out[' '.join(_text(name).split())[:100]] = value
        return out

    def default(self, field):
        return {}

    def display(self, field, value):
        return '; '.join(f'{k}: {v}' for k, v in (value or {}).items())
