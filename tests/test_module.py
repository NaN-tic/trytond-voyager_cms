# This file is part voyager_cms module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction
from trytond.transaction import Transaction


class VoyagerCmsTestCase(ModuleTestCase):
    'Test Voyager Cms module'
    module = 'voyager_cms'

    @with_transaction()
    def test_component_kwargs_use_resource(self):
        Component = Pool().get('www.component')
        Schema = Pool().get('www.schema')

        resource = Schema()

        class DummyModel:
            _fields = {
                'schema': object(),
                'resource': object(),
            }

        kwargs = Component.get_component_kwargs(DummyModel, resource)

        self.assertIs(kwargs['schema'], resource)
        self.assertIs(kwargs['resource'], resource)

    @with_transaction()
    def test_component_resource_resolves_reference_string(self):
        Component = Pool().get('www.component')
        Schema = Pool().get('www.schema')

        schema, = Schema.create([{'title': 'Schema Ref'}])

        class DummyModel:
            _fields = {
                'schema': object(),
                'resource': object(),
            }

        kwargs = Component.get_component_kwargs(
            DummyModel, f'www.schema,{schema.id}')

        self.assertEqual(kwargs['schema'].id, schema.id)
        self.assertEqual(kwargs['resource'].id, schema.id)

    @with_transaction()
    def test_component_kwargs_build_preview_schema_without_resource(self):
        Component = Pool().get('www.component')

        class DummyModel:
            _fields = {
                'schema': object(),
                'resource': object(),
            }

        with Transaction().set_context(voyager_cms_preview=True):
            kwargs = Component.get_component_kwargs(DummyModel)

        self.assertIn('schema', kwargs)
        self.assertIn('resource', kwargs)
        self.assertEqual(kwargs['schema'].title, 'Preview')
        self.assertEqual(kwargs['schema'].menu.name, 'Preview')
        if 'image_url' in kwargs['schema']._fields:
            self.assertEqual(kwargs['schema'].image_url, 'Preview')
        if 'background_hue' in kwargs['schema']._fields:
            self.assertIsNotNone(kwargs['schema'].background_hue)
        if 'date' in kwargs['schema']._fields:
            self.assertIsNotNone(kwargs['schema'].date)

    @with_transaction()
    def test_component_kwargs_skip_preview_schema_for_models_without_schema(self):
        Component = Pool().get('www.component')

        class DummyModel:
            _fields = {}

        with Transaction().set_context(voyager_cms_preview=True):
            kwargs = Component.get_component_kwargs(DummyModel)

        self.assertEqual(kwargs, {})

    @with_transaction()
    def test_normalize_preview_html_replaces_invalid_media_and_links(self):
        Component = Pool().get('www.component')

        content = (
            '<img src="Preview">'
            '<a href="Preview">Link</a>'
            '<svg><path d="Preview"/></svg>'
        )
        normalized = Component._normalize_preview_html(content)

        self.assertIn('data:image/svg+xml,', normalized)
        self.assertIn('href="#"', normalized)
        self.assertIn('d="M12 4v16m8-8H4"', normalized)

del ModuleTestCase
