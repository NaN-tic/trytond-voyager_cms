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
    def test_component_kwargs_use_schema_record(self):
        Component = Pool().get('www.component')
        Schema = Pool().get('www.schema')

        schema = Schema()

        class DummyModel:
            _fields = {
                'schema': object(),
            }

        kwargs = Component.get_component_kwargs(DummyModel, [schema])

        self.assertIs(kwargs['schema'], schema)

    @with_transaction()
    def test_component_schema_uses_first_schema_record(self):
        Component = Pool().get('www.component')
        Schema = Pool().get('www.schema')

        first = Schema()
        second = Schema()

        class DummyModel:
            _fields = {
                'schema': object(),
            }

        kwargs = Component.get_component_kwargs(DummyModel, [first, second])

        self.assertIs(kwargs['schema'], first)

    @with_transaction()
    def test_component_kwargs_build_preview_schema_without_schema(self):
        Component = Pool().get('www.component')

        class DummyModel:
            _fields = {
                'schema': object(),
            }

        with Transaction().set_context(voyager_cms_preview=True):
            kwargs = Component.get_component_kwargs(DummyModel)

        self.assertIn('schema', kwargs)
        schema = kwargs['schema']
        if 'menu' in schema._fields:
            self.assertEqual(schema.menu.name, 'Preview Menu')
        if 'icon' in schema._fields:
            self.assertEqual(schema.icon, 'M12 4v16m8-8H4')
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
    def test_preview_image_uses_svg_data_uri(self):
        Component = Pool().get('www.component')

        preview_image = Component._preview_image()

        self.assertTrue(preview_image.startswith('data:image/svg+xml,'))

del ModuleTestCase
