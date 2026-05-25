# This file is part voyager_cms module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from types import SimpleNamespace
from unittest.mock import patch

from dominate.tags import div
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
            self.assertEqual(schema.icon, 'Preview')
        if 'image_url' in kwargs['schema']._fields:
            self.assertEqual(kwargs['schema'].image_url, 'Preview')
        if 'text' in kwargs['schema']._fields:
            self.assertIn('Preview content', kwargs['schema'].text)
        if 'image_upload' in kwargs['schema']._fields:
            self.assertTrue(kwargs['schema'].image_upload.startswith(
                b'data:image/svg+xml,'))
        if 'background_hue' in kwargs['schema']._fields:
            self.assertIsNotNone(kwargs['schema'].background_hue)
        if 'date' in kwargs['schema']._fields:
            self.assertIsNotNone(kwargs['schema'].date)

    @with_transaction()
    def test_component_kwargs_merge_preview_schema_with_blank_schema(self):
        Component = Pool().get('www.component')
        Schema = Pool().get('www.schema')

        class DummyModel:
            _fields = {
                'schema': object(),
            }

        schema = Schema()
        schema.title = ''

        with Transaction().set_context(voyager_cms_preview=True):
            kwargs = Component.get_component_kwargs(DummyModel, schema)

        self.assertIn('schema', kwargs)
        self.assertNotEqual(kwargs['schema'].title, '')

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

    @with_transaction()
    def test_page_on_change_name_creates_default_uris(self):
        Page = Pool().get('www.page')

        page = Page()
        page.name = 'Hello World'
        page.site = None
        page.state = 'draft'
        page.uris = []

        page.on_change_name()

        self.assertEqual(
            sorted(uri.uri for uri in page.uris),
            ['/draft/ca/hello-world',
             '/draft/en/hello-world',
             '/draft/es/hello-world'])

    @with_transaction()
    def test_fill_uris_only_updates_missing_values(self):
        Page = Pool().get('www.page')

        empty_uri = SimpleNamespace(
            language=SimpleNamespace(code='es'),
            uri=None,
        )
        existing_uri = SimpleNamespace(
            language=SimpleNamespace(code='en'),
            uri='/en/custom',
        )
        unsupported_uri = SimpleNamespace(
            language=SimpleNamespace(code='fr'),
            uri=None,
        )

        changed = Page._fill_uris(
            [empty_uri, existing_uri, unsupported_uri],
            'Hello World',
        )

        self.assertEqual(changed, [empty_uri])
        self.assertEqual(empty_uri.uri, '/es/hello-world')
        self.assertEqual(existing_uri.uri, '/en/custom')
        self.assertIsNone(unsupported_uri.uri)

    @with_transaction()
    def test_state_uri_prefix_is_generic(self):
        Page = Pool().get('www.page')

        self.assertEqual(Page._state_uri_prefix('published'), '')
        self.assertEqual(Page._state_uri_prefix('draft'), '/draft')
        self.assertEqual(Page._state_uri_prefix('review'), '/review')

    @with_transaction()
    def test_publish_button_uses_workflow_transition(self):
        Page = Pool().get('www.page')
        self.assertIn(('draft', 'published'), Page._transitions)

    @with_transaction()
    def test_draft_button_uses_workflow_transition(self):
        Page = Pool().get('www.page')
        self.assertIn(('published', 'draft'), Page._transitions)

    @with_transaction()
    def test_site_allows_only_draft_pages_in_dev(self):
        Site = Pool().get('www.site')
        page = SimpleNamespace(state='draft')

        with patch('trytond.modules.voyager_cms.utils.config.getboolean',
                return_value=False):
            self.assertTrue(Site._allow_page_state_in_environment(page))

        page.state = 'published'
        with patch('trytond.modules.voyager_cms.utils.config.getboolean',
                return_value=False):
            self.assertFalse(Site._allow_page_state_in_environment(page))

    @with_transaction()
    def test_site_allows_only_published_pages_in_production(self):
        Site = Pool().get('www.site')
        page = SimpleNamespace(state='published')

        with patch('trytond.modules.voyager_cms.utils.config.getboolean',
                return_value=True):
            self.assertTrue(Site._allow_page_state_in_environment(page))

        page.state = 'draft'
        with patch('trytond.modules.voyager_cms.utils.config.getboolean',
                return_value=True):
            self.assertFalse(Site._allow_page_state_in_environment(page))

    @with_transaction()
    def test_write_state_resyncs_and_regenerates_uris(self):
        Page = Pool().get('www.page')
        page = SimpleNamespace(id=5, uris=[])

        with patch.object(Page, '_sync_page_uris') as sync_uris, \
                patch.object(Page, '_create_default_uris') as create_uris, \
                patch.object(Page, 'generate_uri') as generate_uri, \
                patch('trytond.modules.voyager_cms.utils.super') as super_mock:
            super_mock.return_value.write.return_value = None
            Page.write([page], {'state': 'published'})

        create_uris.assert_called_once_with([page])
        sync_uris.assert_called_once_with([page], force=True)
        generate_uri.assert_called_once_with([page])

    @with_transaction()
    def test_find_published_pages_to_replace_uses_linked_page_first(self):
        Page = Pool().get('www.page')
        linked_page = SimpleNamespace(id=9)
        page = SimpleNamespace(id=5, published_page=linked_page)

        self.assertEqual(Page._find_published_pages_to_replace(page), [linked_page])

    @with_transaction()
    def test_delete_relations_use_expected_ondelete_rules(self):
        pool = Pool()
        Page = pool.get('www.page')
        PageURI = pool.get('www.page.uri')
        Element = pool.get('www.element')
        Schema = pool.get('www.schema')
        Menu = pool.get('www.menu')
        URI = pool.get('www.uri')
        Site = pool.get('www.site')
        SiteLang = pool.get('www.site.lang')

        self.assertEqual(Page._fields['site'].ondelete, 'CASCADE')
        self.assertEqual(PageURI._fields['page'].ondelete, 'CASCADE')
        self.assertEqual(Element._fields['page'].ondelete, 'CASCADE')
        self.assertEqual(Schema._fields['component'].ondelete, 'CASCADE')
        self.assertEqual(URI._fields['site'].ondelete, 'CASCADE')
        self.assertEqual(Menu._fields['site'].ondelete, 'CASCADE')
        self.assertEqual(Menu._fields['uri'].ondelete, 'SET NULL')
        self.assertEqual(Menu._fields['component'].ondelete, 'SET NULL')
        self.assertEqual(Site._fields['header'].ondelete, 'SET NULL')
        self.assertEqual(Site._fields['footer'].ondelete, 'SET NULL')
        self.assertEqual(Site._fields['layout'].ondelete, 'SET NULL')
        self.assertEqual(SiteLang._fields['site'].ondelete, 'CASCADE')

    @with_transaction()
    def test_render_with_site_layout_keeps_content_without_chrome(self):
        Element = Pool().get('www.element')

        class DummyElement:
            @classmethod
            def get_element_kwargs(cls, model, schema=None):
                return {}

        class DummyHeader:
            _fields = {}

            def __init__(self, **kwargs):
                pass

            def tag(self):
                return div('Header Chrome')

        class DummyFooter:
            _fields = {}

            def __init__(self, **kwargs):
                pass

            def tag(self):
                return div('Footer Chrome')

        def fake_get(pool, name):
            mapping = {
                'www.element': DummyElement,
                'test.header': DummyHeader,
                'test.footer': DummyFooter,
            }
            return mapping[name]

        site = SimpleNamespace(
            header=SimpleNamespace(
                model=SimpleNamespace(name='test.header'),
                schema=None),
            footer=SimpleNamespace(
                model=SimpleNamespace(name='test.footer'),
                schema=None),
            layout=None,
        )

        with patch.object(Pool, 'get', new=fake_get):
            rendered = Element.render_with_site_layout(
                site, div('Body Content'), 'Page Title')

        html = rendered.render()
        self.assertIn('Body Content', html)
        self.assertNotIn('Header Chrome', html)
        self.assertNotIn('Footer Chrome', html)

    @with_transaction()
    def test_render_preview_content_hides_header_and_footer(self):
        Element = Pool().get('www.element')

        class DummyElement:
            @classmethod
            def get_element_kwargs(cls, model, schema=None):
                return {}

        class DummyComponent:
            _fields = {}

            def __init__(self, **kwargs):
                pass

            def tag(self):
                return div('Preview Body')

        class DummyHeader:
            _fields = {}

            def __init__(self, **kwargs):
                pass

            def tag(self):
                return div('Header Chrome')

        class DummyFooter:
            _fields = {}

            def __init__(self, **kwargs):
                pass

            def tag(self):
                return div('Footer Chrome')

        def fake_get(pool, name):
            mapping = {
                'www.element': DummyElement,
                'test.component': DummyComponent,
                'test.header': DummyHeader,
                'test.footer': DummyFooter,
            }
            return mapping[name]

        site = SimpleNamespace(
            header=SimpleNamespace(
                model=SimpleNamespace(name='test.header'),
                schema=None),
            footer=SimpleNamespace(
                model=SimpleNamespace(name='test.footer'),
                schema=None),
            layout=None,
        )
        element = SimpleNamespace(
            model=SimpleNamespace(name='test.component'),
            schema=None,
            page=SimpleNamespace(site=site, name='Page Name'),
            name='Element Name',
        )

        with patch.object(Pool, 'get', new=fake_get):
            html = Element.render_preview_content(element)

        self.assertIn('Preview Body', html)
        self.assertNotIn('Header Chrome', html)
        self.assertNotIn('Footer Chrome', html)

del ModuleTestCase
