import json
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from . import blueprint, records, schema
from .models import App, Field, Link, Row, Table

PARTS = Path(settings.BASE_DIR) / 'blueprints' / 'parts.json'


def install_parts(test):
    result = blueprint.apply(json.loads(PARTS.read_text(encoding='utf-8')))
    test.assertTrue(result.ok, result.errors)
    return Table.objects.get(key='categories'), Table.objects.get(app__key='parts', key='parts')


class BlueprintTests(TestCase):
    def test_parts_blueprint_installs_and_reimport_changes_nothing(self):
        categories, parts = install_parts(self)
        self.assertEqual(categories.tree_field, 'parent')
        self.assertEqual(parts.display_field, 'part_number')
        self.assertEqual([v.key for v in parts.views.all()], ['active', 'archived', 'all'])
        again = blueprint.apply(json.loads(PARTS.read_text(encoding='utf-8')))
        self.assertTrue(again.ok, again.errors)
        self.assertEqual(again.summary(), [])

    def test_export_round_trip_changes_nothing(self):
        install_parts(self)
        result = blueprint.apply(blueprint.export(), dry_run=True)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.summary(), [])

    def test_dry_run_and_errors_change_nothing(self):
        bp = {'osmia_blueprint': 1, 'apps': [{'key': 'shop', 'tables': [{'key': 'items', 'fields': [
            {'key': 'name', 'type': 'text'}]}]}]}
        result = blueprint.apply(bp, dry_run=True)
        self.assertTrue(result.ok)
        self.assertIn('New app "Shop"', result.summary())
        self.assertFalse(App.objects.exists())
        bp['apps'][0]['tables'][0]['fields'].append({'key': 'bad', 'type': 'nope'})
        result = blueprint.apply(bp)
        self.assertFalse(result.ok)
        self.assertIn('unknown type', result.errors[0])
        self.assertFalse(App.objects.exists())
        self.assertIn('Nothing was changed', result.errors_for_ai())

    def test_bad_link_target_and_unknown_properties_are_reported(self):
        result = blueprint.apply({'osmia_blueprint': 1, 'colour': 'red', 'apps': [{'key': 'a', 'tables': [
            {'key': 't', 'fields': [{'key': 'other', 'type': 'link', 'options': {'target': 'a.missing'}}]}]}]})
        self.assertFalse(result.ok)
        text = ' '.join(result.errors)
        self.assertIn('colour', text)
        self.assertIn('a.missing', text)

    def test_type_change_refused_once_table_has_rows(self):
        _, parts = install_parts(self)
        records.save_row(parts, {'part_number': 'P1'})
        bp = {'osmia_blueprint': 1, 'apps': [{'key': 'parts', 'tables': [{'key': 'parts', 'fields': [
            {'key': 'unit', 'type': 'number'}]}]}]}
        result = blueprint.apply(bp)
        self.assertFalse(result.ok)
        bp['apps'][0]['tables'][0]['fields'][0]['type'] = 'long_text'
        self.assertTrue(blueprint.apply(bp).ok)

    def test_new_field_backfills_default(self):
        _, parts = install_parts(self)
        row = records.save_row(parts, {'part_number': 'P1'})
        blueprint.apply({'osmia_blueprint': 1, 'apps': [{'key': 'parts', 'tables': [{'key': 'parts', 'fields': [
            {'key': 'stocked', 'type': 'checkbox', 'options': {'default': True}}]}]}]})
        row.refresh_from_db()
        self.assertIs(row.data['stocked'], True)

    def test_rows_with_refs_names_and_attributes(self):
        categories, parts = install_parts(self)
        result = blueprint.apply({'osmia_blueprint': 1, 'rows': [
            {'table': 'parts.categories', 'ref': 'hw', 'values': {'name': 'Hardware', 'attributes': [{'name': 'Material'}]}},
            {'table': 'parts.categories', 'ref': 'fa', 'values': {'name': 'Fasteners', 'parent': '@hw',
                                                                  'attributes': [{'name': 'Thread', 'required': True}]}},
            {'table': 'parts.parts', 'values': {'part_number': 'M3-12', 'category': 'Hardware > Fasteners',
                                                'attributes': {'thread': 'M3', 'Material': 'Steel'}}},
        ]})
        self.assertTrue(result.ok, result.errors)
        part = parts.rows.get()
        self.assertEqual(part.data['attributes'], {'Thread': 'M3', 'Material': 'Steel'})
        self.assertIn('hardware > fasteners', part.search_text)
        # Importing the same rows again updates them instead of adding more.
        again = blueprint.apply({'osmia_blueprint': 1, 'rows': [
            {'table': 'parts.parts', 'values': {'part_number': 'm3-12', 'description': 'Screw'}}]})
        self.assertTrue(again.ok, again.errors)
        self.assertEqual(parts.rows.count(), 1)

    def test_parse_text_finds_json_in_an_ai_answer(self):
        answer = 'Sure! Here it is:\n\n```json\n{"osmia_blueprint": 1, "apps": []}\n```\nEnjoy.'
        self.assertEqual(blueprint.parse_text(answer), {'osmia_blueprint': 1, 'apps': []})
        self.assertEqual(blueprint.parse_text('﻿{"osmia_blueprint": 1}'), {'osmia_blueprint': 1})
        with self.assertRaises(ValueError):
            blueprint.parse_text('no json here')


class PartsTests(TestCase):
    def setUp(self):
        self.categories, self.parts = install_parts(self)
        self.hardware = records.save_row(self.categories, {
            'name': 'Hardware', 'attributes': [{'name': 'Material', 'hint': 'Steel'}]})
        self.fasteners = records.save_row(self.categories, {
            'name': 'Fasteners', 'parent': [self.hardware.pk],
            'attributes': [{'name': 'Thread', 'required': True}, {'name': 'Length'}]})

    def test_new_part_gets_defaults(self):
        part = records.save_row(self.parts, {'part_number': 'P1'})
        self.assertEqual(part.data['unit'], 'pcs')
        self.assertIs(part.data['is_active'], True)

    def test_part_number_is_required_and_unique_ignoring_case(self):
        with self.assertRaises(records.RowErrors) as ctx:
            records.save_row(self.parts, {'description': 'x'})
        self.assertIn('part_number', ctx.exception.errors)
        records.save_row(self.parts, {'part_number': 'ABC-1'})
        with self.assertRaises(records.RowErrors) as ctx:
            records.save_row(self.parts, {'part_number': 'abc-1'})
        self.assertIn('already', ctx.exception.errors['part_number'][0])

    def test_attributes_follow_category_including_parents(self):
        defs = records.attribute_defs_for(self.parts.fields.get(key='attributes'), self.fasteners.pk)
        self.assertEqual([d['name'] for d in defs], ['Thread', 'Length', 'Material'])
        with self.assertRaises(records.RowErrors) as ctx:
            records.save_row(self.parts, {'part_number': 'S1', 'category': [self.fasteners.pk],
                                          'attributes': {'Length': '12 mm'}})
        self.assertIn('Thread', ctx.exception.errors['attributes'][0])
        part = records.save_row(self.parts, {'part_number': 'S1', 'category': [self.fasteners.pk],
                                             'attributes': {'Thread': 'M3', 'Colour': 'red'}})
        self.assertEqual(part.data['attributes'], {'Thread': 'M3'})
        # Moving the part to Hardware drops attributes Hardware doesn't have.
        part = records.save_row(self.parts, {'category': [self.hardware.pk]}, row=part)
        self.assertEqual(part.data['attributes'], {})

    def test_category_labels_show_the_path_and_cycles_are_refused(self):
        self.assertEqual(records.Labels().label(self.fasteners), 'Hardware > Fasteners')
        with self.assertRaises(records.RowErrors):
            records.save_row(self.categories, {'parent': [self.fasteners.pk]}, row=self.hardware)
        with self.assertRaises(records.RowErrors):
            records.save_row(self.categories, {'parent': [self.hardware.pk]}, row=self.hardware)

    def test_mates_with_is_two_way(self):
        plug = records.save_row(self.parts, {'part_number': 'PLUG'})
        socket = records.save_row(self.parts, {'part_number': 'SOCKET'})
        records.save_row(self.parts, {'mates_with': [socket.pk]}, row=plug)
        field = self.parts.fields.get(key='mates_with')
        self.assertTrue(Link.objects.filter(field=field, source=socket, target=plug).exists())
        records.save_row(self.parts, {'mates_with': []}, row=plug)
        self.assertFalse(Link.objects.filter(field=field).exists())

    def test_search_and_category_filter_include_sub_categories(self):
        screw = records.save_row(self.parts, {'part_number': 'SCREW', 'category': [self.fasteners.pk],
                                              'attributes': {'Thread': 'M4'}})
        records.save_row(self.parts, {'part_number': 'OTHER'})
        found = records.filtered_rows(self.parts, q='fasteners m4')
        self.assertEqual(list(found), [screw])
        category = self.parts.fields.get(key='category')
        in_hardware = records.filtered_rows(self.parts, [{'field': 'category', 'op': 'is', 'value': self.hardware.pk}])
        self.assertEqual(list(in_hardware), [screw])
        self.assertEqual(records.filtered_rows(self.parts, [{'field': category.key, 'op': 'empty'}]).count(), 1)
        # Renaming the category updates what parts are found by.
        records.save_row(self.categories, {'name': 'Screws'}, row=self.fasteners)
        self.assertEqual(list(records.filtered_rows(self.parts, q='screws')), [screw])

    def test_archived_view_filters_and_sorting(self):
        records.save_row(self.parts, {'part_number': 'B'})
        records.save_row(self.parts, {'part_number': 'a'})
        records.save_row(self.parts, {'part_number': 'C', 'is_active': False})
        active = self.parts.views.get(key='active').config
        rows = records.filtered_rows(self.parts, active['filters'], sort=active['sort'])
        self.assertEqual([r.data['part_number'] for r in rows], ['a', 'B'])
        archived = self.parts.views.get(key='archived').config
        self.assertEqual([r.data['part_number'] for r in records.filtered_rows(self.parts, archived['filters'])], ['C'])

    def test_deleting_fields_and_tables(self):
        self.assertIn('used by', schema.delete_field(self.categories.fields.get(key='attributes')))
        self.assertIn('linked from', schema.delete_table(self.categories))
        part = records.save_row(self.parts, {'part_number': 'P1', 'notes': 'hello'})
        self.assertIsNone(schema.delete_field(self.parts.fields.get(key='notes')))
        part.refresh_from_db()
        self.assertNotIn('notes', part.data)


@override_settings(MEDIA_ROOT=Path(settings.BASE_DIR) / 'media-test')
class PageTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(Path(settings.BASE_DIR) / 'media-test', ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.categories, self.parts = install_parts(self)
        User = get_user_model()
        self.staff = User.objects.create_user('boss', password='pw', is_staff=True)
        self.worker = User.objects.create_user('worker', password='pw')
        self.client.force_login(self.staff)

    def test_pages_load(self):
        cat = records.save_row(self.categories, {'name': 'Connectors'})
        part = records.save_row(self.parts, {'part_number': 'P1', 'category': [cat.pk]})
        for url in [reverse('home'), self.parts.get_absolute_url(), reverse('app_detail', args=['parts']),
                    part.get_absolute_url(), reverse('row_edit', args=[part.pk]), reverse('row_create', args=[self.parts.pk]),
                    reverse('table_settings', args=[self.parts.pk]), reverse('field_create', args=[self.parts.pk]),
                    reverse('view_edit', args=[self.parts.views.first().pk]), reverse('ai_prompt'), reverse('ai_import'),
                    cat.get_absolute_url()]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        page = self.client.get(cat.get_absolute_url()).content.decode()
        self.assertIn('Parts', page)
        self.assertIn('P1', page)
        # A category also lists the parts of its sub-categories.
        top = records.save_row(self.categories, {'name': 'Electrical'})
        records.save_row(self.categories, {'parent': [top.pk]}, row=cat)
        self.assertContains(self.client.get(top.get_absolute_url()), 'P1')

    def test_row_form_saves_links_and_attributes(self):
        cat = records.save_row(self.categories, {'name': 'Screws', 'attributes': [{'name': 'Thread', 'required': True}]})
        response = self.client.post(reverse('row_create', args=[self.parts.pk]), {
            'part_number': 'S-1', 'description': 'Screw', 'category': str(cat.pk), 'attributes::Thread': 'M3',
            'unit': 'pcs', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        part = self.parts.rows.get()
        self.assertEqual(part.data['attributes'], {'Thread': 'M3'})
        response = self.client.post(reverse('row_edit', args=[part.pk]), {
            'part_number': 'S-1', 'category': str(cat.pk), 'attributes::Thread': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Required: Thread')

    def test_grid_cell_edit(self):
        part = records.save_row(self.parts, {'part_number': 'P1'})
        url = reverse('api_cell', args=[part.pk])
        response = self.client.post(url, {'field': 'description', 'value': 'Nut'}, content_type='application/json')
        self.assertEqual(response.json()['text'], 'Nut')
        records.save_row(self.parts, {'part_number': 'P2'})
        response = self.client.post(url, {'field': 'part_number', 'value': 'p2'}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('already', response.json()['error'])

    def test_builder_creates_table_field_and_view(self):
        self.client.post(reverse('table_create', args=['parts']), {'name': 'Suppliers'})
        table = Table.objects.get(key='suppliers')
        self.assertEqual(table.display_field, 'name')
        self.client.post(reverse('field_create', args=[table.pk]), {
            'name': 'Rating', 'type': 'select', 'opt_select_choices': 'Good\nBad'})
        field = table.fields.get(key='rating')
        self.assertEqual(field.options['choices'], ['Good', 'Bad'])
        self.client.post(reverse('field_create', args=[self.parts.pk]), {
            'name': 'Suppliers', 'type': 'link', 'opt_link_target': 'parts.suppliers', 'opt_link_multiple': 'on'})
        self.assertTrue(self.parts.fields.filter(key='suppliers', type='link').exists())
        self.client.post(reverse('view_create', args=[table.pk]), {
            'name': 'Good ones', 'columns': ['name'], 'filter_field': ['rating'], 'filter_op': ['equals'],
            'filter_value': ['Good'], 'sort_field': ['name'], 'sort_dir': ['asc']})
        view = table.views.get(key='good_ones')
        self.assertEqual(view.config['filters'], [{'field': 'rating', 'op': 'equals', 'value': 'Good'}])

    def test_non_staff_can_edit_data_but_not_build(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('field_create', args=[self.parts.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('ai_import')).status_code, 403)
        self.assertEqual(self.client.get(self.parts.get_absolute_url()).status_code, 200)
        response = self.client.post(reverse('row_create', args=[self.parts.pk]), {'part_number': 'W1'})
        self.assertEqual(response.status_code, 302)

    def test_import_upload_preview_then_apply(self):
        answer = ('Here you go:\n```json\n' + json.dumps({'osmia_blueprint': 1, 'notes': 'Added datasheets.', 'apps': [
            {'key': 'parts', 'tables': [{'key': 'parts', 'fields': [{'key': 'datasheet', 'name': 'Datasheet',
                                                                      'type': 'url'}]}]}]}) + '\n```')
        upload = SimpleUploadedFile('answer.md', answer.encode(), content_type='text/markdown')
        response = self.client.post(reverse('ai_import'), {'file': upload})
        self.assertContains(response, 'New field &quot;Datasheet&quot;')
        self.assertContains(response, 'Added datasheets.')
        self.assertFalse(self.parts.fields.filter(key='datasheet').exists())
        response = self.client.post(reverse('ai_import'), {
            'action': 'apply', 'blueprint_json': response.context['blueprint_json']})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.parts.fields.filter(key='datasheet').exists())

    def test_import_errors_offer_text_for_the_ai(self):
        response = self.client.post(reverse('ai_import'), {'text': '{"osmia_blueprint": 1, "apps": [{"key": "Bad Key"}]}'})
        self.assertContains(response, 'Copy errors for the AI')

    def test_prompt_contains_guide_setup_and_request(self):
        response = self.client.post(reverse('ai_prompt'), {'request': 'Add suppliers', 'apps': ['parts']})
        prompt = response.context['prompt_text']
        self.assertIn('Osmia blueprint guide', prompt)
        self.assertIn('"part_number"', prompt)
        self.assertIn('Add suppliers', prompt)

    def test_library_install_previews(self):
        App.objects.all().delete()
        response = self.client.get(reverse('library_install', args=['parts']))
        self.assertContains(response, 'Apply these changes')
        self.assertContains(response, f'action="{reverse("ai_import")}"')

    def test_image_upload(self):
        from PIL import Image
        part = records.save_row(self.parts, {'part_number': 'P1'})
        buffer = BytesIO()
        Image.new('RGB', (3000, 1000), 'red').save(buffer, 'JPEG')
        field = self.parts.fields.get(key='images')
        upload = SimpleUploadedFile('photo.jpg', buffer.getvalue(), content_type='image/jpeg')
        self.client.post(reverse('images_upload', args=[part.pk, field.pk]), {'images': [upload]})
        image = part.attachments.get()
        with Image.open(image.file.path) as stored:
            self.assertEqual(stored.size, (2000, 667))
        self.assertEqual(self.client.get(self.parts.get_absolute_url()).status_code, 200)

    def test_api_search_for_picker(self):
        hw = records.save_row(self.categories, {'name': 'Hardware'})
        records.save_row(self.categories, {'name': 'Fasteners', 'parent': [hw.pk]})
        data = self.client.get(reverse('api_search', args=[self.categories.pk]), {'q': 'fast'}).json()
        self.assertEqual([r['label'] for r in data['rows']], ['Hardware > Fasteners'])
        data = self.client.get(reverse('api_search', args=[self.categories.pk]), {'exclude': hw.pk}).json()
        self.assertEqual(data['rows'], [])


class KeyTests(TestCase):
    def test_make_key(self):
        self.assertEqual(schema.make_key('Part number'), 'part_number')
        self.assertEqual(schema.make_key('Part number', ['part_number']), 'part_number_2')
        self.assertEqual(schema.make_key('2nd'), 'item_2nd')
        self.assertEqual(Field.objects.count(), 0)
        self.assertEqual(Row.objects.count(), 0)
