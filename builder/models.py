"""Everything users build is data: apps hold tables, tables hold fields and
views (saved grids), and rows keep their values in a JSON column keyed by
field key. Links between rows and images have tables of their own.

Keys (app, table, field, view) never change once created, so blueprints and
stored references ("parts.categories") stay valid when things are renamed.
"""
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.urls import reverse

KEY_PATTERN = r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$'
key_validator = RegexValidator(
    KEY_PATTERN, 'Use lower-case letters, digits and single underscores, starting with a letter.',
)


class App(models.Model):
    key = models.CharField(max_length=50, unique=True, validators=[key_validator])
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=16, blank=True, help_text='An emoji shown next to the name.')
    description = models.TextField(blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('app_detail', args=[self.key])


class Table(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name='tables')
    key = models.CharField(max_length=50, validators=[key_validator])
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    # Field keys with a special role (blank = none):
    display_field = models.CharField(max_length=50, blank=True, help_text='Names a row in links and lists.')
    subtitle_field = models.CharField(max_length=50, blank=True, help_text='Shown next to the name in pickers.')
    tree_field = models.CharField(
        max_length=50, blank=True,
        help_text='A link to this same table that makes rows nested (e.g. a category inside a category).',
    )
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['app', 'order', 'id']
        constraints = [models.UniqueConstraint(fields=['app', 'key'], name='unique_table_key_per_app')]

    def __str__(self):
        return self.name

    @property
    def ref(self):
        return f'{self.app.key}.{self.key}'

    def get_absolute_url(self):
        return reverse('table', args=[self.pk])


class Field(models.Model):
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name='fields')
    key = models.CharField(max_length=50, validators=[key_validator])
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=30)
    required = models.BooleanField(default=False)
    unique = models.BooleanField(default=False)
    help_text = models.CharField(max_length=300, blank=True)
    options = models.JSONField(default=dict, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['table', 'key'], name='unique_field_key_per_table')]

    def __str__(self):
        return self.name

    @property
    def kind(self):
        from . import fieldtypes
        return fieldtypes.get(self.type)


class View(models.Model):
    """A saved grid: which columns, in what order, filtered and sorted how."""

    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name='views')
    key = models.CharField(max_length=50, validators=[key_validator])
    name = models.CharField(max_length=100)
    config = models.JSONField(default=dict, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['table', 'key'], name='unique_view_key_per_table')]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('table_view', args=[self.table_id, self.pk])


class Row(models.Model):
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name='rows')
    data = models.JSONField(default=dict, blank=True)
    # Lower-cased values and link labels, kept up to date by records.save_row.
    search_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['id']

    def __str__(self):
        from .records import Labels
        return Labels().label(self)

    def get_absolute_url(self):
        return reverse('row_detail', args=[self.pk])


class Link(models.Model):
    """One row pointing at another through a link field. Links of a two-way
    (symmetric) field are stored in both directions."""

    field = models.ForeignKey(Field, on_delete=models.CASCADE, related_name='links')
    source = models.ForeignKey(Row, on_delete=models.CASCADE, related_name='links_out')
    target = models.ForeignKey(Row, on_delete=models.CASCADE, related_name='links_in')
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['field', 'source', 'target'], name='unique_link')]


class Attachment(models.Model):
    """An image on a row, for an images field. The first one is the cover."""

    row = models.ForeignKey(Row, on_delete=models.CASCADE, related_name='attachments')
    field = models.ForeignKey(Field, on_delete=models.CASCADE, related_name='attachments')
    file = models.ImageField(upload_to='images/%Y/%m/')
    caption = models.CharField(max_length=200, blank=True)
    order = models.IntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']
