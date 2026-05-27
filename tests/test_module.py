# This file is part voyager_cms module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from types import SimpleNamespace
from unittest.mock import patch

from dominate.tags import div
from trytond.model import fields
from trytond.modules.voyager_cms import utils as voyager_utils
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
            self.assertIsNone(schema.menu)
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
    def test_element_kwargs_keep_preview_schema_when_disabled(self):
        Element = Pool().get('www.element')

        class DummyModel:
            _fields = {
                'schema': object(),
            }

        with Transaction().set_context(voyager_cms_preview=True):
            kwargs = Element.get_element_kwargs(
                DummyModel, show_preview_fields=False)

        self.assertIn('schema', kwargs)

    @with_transaction()
    def test_element_kwargs_keep_preview_with_schema_when_disabled(self):
        Element = Pool().get('www.element')
        Schema = Pool().get('www.schema')

        class DummyModel:
            _fields = {
                'schema': object(),
            }

        schema = Schema()
        schema.title = ''

        with Transaction().set_context(voyager_cms_preview=True):
            kwargs = Element.get_element_kwargs(
                DummyModel, schema, show_preview_fields=False)

        self.assertIn('schema', kwargs)

    @with_transaction()
    def test_render_element_content_still_renders_when_preview_disabled(self):
        Element = Pool().get('www.element')

        with Transaction().set_context(voyager_cms_preview=True):
            content = Element.render_element_content(
                'test.component', show_preview_fields=False)

        self.assertTrue(getattr(content, 'children', []))

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
    def test_preview_one2many_fields_are_empty_lists(self):
        Element = Pool().get('www.element')

        preview_value = Element._preview_value_for_field(
            'children', fields.One2Many('test.child', 'parent', 'Children'))

        self.assertEqual(preview_value, [])

    @with_transaction()
    def test_preview_many2one_fields_are_none(self):
        Element = Pool().get('www.element')

        preview_value = Element._preview_value_for_field(
            'menu', fields.Many2One('www.menu', 'Menu'))

        self.assertIsNone(preview_value)

    @with_transaction()
    def test_preview_one2many_fields_do_not_merge_real_values(self):
        Element = Pool().get('www.element')
        preview_schema = SimpleNamespace(
            _fields={
                'children': fields.One2Many(
                    'test.child', 'parent', 'Children'),
            },
            children=[],
        )
        schema = SimpleNamespace(children=['real'])

        with patch.object(
                Element, '_build_preview_schema',
                return_value=preview_schema):
            merged = Element._build_preview_schema_with_values(schema)

        self.assertEqual(merged.children, [])

    @with_transaction()
    def test_build_preview_schema_sets_many2one_fields_to_none(self):
        Element = Pool().get('www.element')

        class FakeSchema:
            _fields = {
                'menu': fields.Many2One('www.menu', 'Menu'),
            }

            def __init__(self):
                self.menu = 'unexpected'

        with patch('trytond.modules.voyager_cms.utils.Pool') as PoolMock:
            PoolMock.return_value.get.return_value = FakeSchema
            preview_schema = Element._build_preview_schema()

        self.assertIsNone(preview_schema.menu)

    @with_transaction()
    def test_element_show_preview_fields_defaults_to_true(self):
        Element = Pool().get('www.element')

        self.assertTrue(Element.default_show_preview_fields())

    @with_transaction()
    def test_element_model_ids_include_only_componentcms_models(self):
        Element = Pool().get('www.element')

        class PlainModel:
            pass

        class DummyComponent(voyager_utils.ComponentCMS):
            __name__ = 'test.component'

        with patch('trytond.modules.voyager_cms.utils.Pool') as PoolMock:
            pool = PoolMock.return_value
            Model = SimpleNamespace(search=lambda domain: [
                SimpleNamespace(id=7),
            ])
            pool.get.return_value = Model
            pool.iterobject.return_value = iter([
                ('test.component', DummyComponent),
                ('test.plain', PlainModel),
            ])

            model_ids = Element._element_model_ids()

        self.assertEqual(model_ids, [7])

    @with_transaction()
    def test_element_get_valid_models_returns_same_ids_for_each_record(self):
        Element = Pool().get('www.element')
        first = SimpleNamespace(id=1)
        second = SimpleNamespace(id=2)

        with patch.object(Element, '_element_model_ids',
                return_value=[7, 9]):
            result = Element.get_valid_models([first, second], 'valid_models')

        self.assertEqual(result, {
            1: [7, 9],
            2: [7, 9],
        })

    @with_transaction()
    def test_element_default_valid_models_uses_componentcms_model_ids(self):
        Element = Pool().get('www.element')

        with patch.object(Element, '_element_model_ids',
                return_value=[7, 9]):
            model_ids = Element.default_valid_models()

        self.assertEqual(model_ids, [7, 9])

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
    def test_page_uris_field_is_editable_in_draft(self):
        Page = Pool().get('www.page')

        field = Page.uris.field
        self.assertFalse(field.readonly)
        self.assertIn('readonly', field.states)

    @with_transaction()
    def test_page_set_uris_forwards_writes_to_www_uri(self):
        Page = Pool().get('www.page')

        page = SimpleNamespace(id=5, site=SimpleNamespace(id=3))
        uri_record = SimpleNamespace(id=1)

        pool_mock = SimpleNamespace()
        uri_model = SimpleNamespace()
        uri_model.search = lambda *args, **kwargs: [uri_record]
        uri_model.write = lambda *args, **kwargs: None
        uri_model.delete = lambda *args, **kwargs: None
        uri_model.create = lambda *args, **kwargs: None

        with patch('trytond.modules.voyager_cms.utils.Pool') as PoolMock:
            PoolMock.return_value = pool_mock
            pool_mock.get = lambda name: uri_model
            with patch.object(uri_model, 'write') as write_mock:
                Page.set_uris(
                    [page],
                    'uris',
                    [('write', [1], {'uri': '/draft/en/test'})],
                )

        write_mock.assert_called_once()
        args, kwargs = write_mock.call_args
        self.assertEqual([r.id for r in args[0]], [1])
        self.assertEqual(args[1], {'uri': '/draft/en/test'})

    @with_transaction()
    def test_page_set_uris_supports_numeric_o2m_commands(self):
        Page = Pool().get('www.page')

        page = SimpleNamespace(id=5, site=SimpleNamespace(id=3))
        uri_record = SimpleNamespace(id=1)

        pool_mock = SimpleNamespace()
        uri_model = SimpleNamespace()
        uri_model.search = lambda *args, **kwargs: [uri_record]
        uri_model.write = lambda *args, **kwargs: None

        with patch('trytond.modules.voyager_cms.utils.Pool') as PoolMock:
            PoolMock.return_value = pool_mock
            pool_mock.get = lambda name: uri_model
            with patch.object(uri_model, 'write') as write_mock:
                Page.set_uris(
                    [page],
                    'uris',
                    [(1, 1, {'uri': '/draft/en/test'})],
                )

        write_mock.assert_called_once()
        args, kwargs = write_mock.call_args
        self.assertEqual([r.id for r in args[0]], [1])
        self.assertEqual(args[1], {'uri': '/draft/en/test'})

    @with_transaction()
    def test_page_generate_uri_uses_selected_main_uri_language(self):
        Page = Pool().get('www.page')

        class LangRecord:
            def __init__(self, id, code):
                self.id = id
                self.code = code

        class LangModel:
            def __call__(self, id):
                return {1: es, 2: en}[id]

            def search(self, domain):
                # Called by Page.on_change_with_available_languages; return both.
                return [es, en]

        class UriRecord:
            def __init__(self, id, uri, language):
                self.id = id
                self.uri = uri
                self.language = language
                self.main_uri = None

        class UriModel:
            def __init__(self):
                self._records = []
                self._next_id = 1

            def search(self, domain, order=None, limit=None):
                # Only searches on resource/site, return everything in memory.
                return list(self._records)

            def create(self, values_list):
                created = []
                for values in values_list:
                    language = {1: es, 2: en}[values['language']]
                    rec = UriRecord(
                        self._next_id, values['uri'], language)
                    self._next_id += 1
                    self._records.append(rec)
                    created.append(rec)
                return created

            def write(self, uris, values):
                for uri in uris:
                    for k, v in values.items():
                        if k == 'language':
                            uri.language = {1: es, 2: en}[v]
                        elif k == 'main_uri':
                            uri.main_uri = v
                        else:
                            setattr(uri, k, v)

            def delete(self, uris):
                for uri in uris:
                    if uri in self._records:
                        self._records.remove(uri)

        class ModelModel:
            def search(self, domain, limit=None):
                return [SimpleNamespace(id=99)]

        es = LangRecord(1, 'es')
        en = LangRecord(2, 'en')
        uri_model = UriModel()

        site = SimpleNamespace(id=3, langs=[es, en])
        page = SimpleNamespace(
            __name__='www.page',
            id=5,
            name='Hello World',
            site=site,
            state='draft',
            main_uri_language=en,
            rec_name='Hello World',
        )

        pool_mock = SimpleNamespace()

        def get_model(name):
            if name == 'www.uri':
                return uri_model
            if name == 'ir.model':
                return ModelModel()
            if name == 'ir.lang':
                return LangModel()
            raise AssertionError(name)

        with patch('trytond.modules.voyager_cms.utils.Pool') as PoolMock:
            PoolMock.return_value = pool_mock
            pool_mock.get = get_model
            Page.generate_uri([page])

        # Ensure the URI for the selected language ('en') becomes the main URI.
        by_code = {u.language.code: u for u in uri_model._records}
        self.assertIsNone(by_code['en'].main_uri)
        self.assertEqual(by_code['es'].main_uri, by_code['en'].id)

    @with_transaction()
    def test_generate_uri_preserves_manually_edited_uri(self):
        Page = Pool().get('www.page')

        page = SimpleNamespace(
            __name__='www.page',
            id=5,
            site=SimpleNamespace(id=3),
            state='draft',
            name='Hello World',
            rec_name='Hello World',
        )

        existing_uri = SimpleNamespace(
            id=10,
            uri='/draft/en/test',
            language=SimpleNamespace(code='en'),
            site=page.site,
            main_uri=None,
        )

        written = []

        def uri_search(domain, limit=None, order=None):
            # existing record query
            if ('resource', '=', 'www.page,5') in domain:
                return [existing_uri]
            # duplicate search query
            return []

        uri_model = SimpleNamespace(
            search=uri_search,
            delete=lambda records: None,
            create=lambda values: [],
            write=lambda records, values: written.append((records, values)),
        )

        endpoint_model = SimpleNamespace(name='www.content.wrapper')
        endpoint_model.id = 99
        model_model = SimpleNamespace(search=lambda *a, **k: [endpoint_model])

        class DummyLang:
            def __init__(self, _id):
                self.id = _id
                self.code = 'en'

        pool_mock = SimpleNamespace(
            get=lambda name: {
                'www.uri': uri_model,
                'ir.model': model_model,
                'ir.lang': DummyLang,
            }[name]
        )

        with patch('trytond.modules.voyager_cms.utils.Pool') as PoolMock, \
                patch.object(Page, '_default_uris',
                    return_value=[{'language': 1, 'uri': '/draft/en/hello-world'}]):
            PoolMock.return_value = pool_mock
            Page.generate_uri([page])

        self.assertTrue(written)
        # First write should keep the manually edited URI and set endpoint.
        self.assertEqual(written[0][1]['uri'], '/draft/en/test')
        self.assertIn('endpoint', written[0][1])

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
        page = SimpleNamespace(id=5)

        with patch.object(Page, 'generate_uri') as generate_uri, \
                patch('trytond.modules.voyager_cms.utils.super') as super_mock:
            super_mock.return_value.write.return_value = None
            Page.write([page], {'state': 'published'})

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
        Element = pool.get('www.element')
        Schema = pool.get('www.schema')
        Menu = pool.get('www.menu')
        URI = pool.get('www.uri')
        Site = pool.get('www.site')
        SiteLang = pool.get('www.site.lang')

        self.assertEqual(Page._fields['site'].ondelete, 'CASCADE')
        self.assertEqual(Element._fields['page'].ondelete, 'CASCADE')
        self.assertEqual(Schema._fields['element'].ondelete, 'CASCADE')
        self.assertEqual(URI._fields['site'].ondelete, 'CASCADE')
        self.assertEqual(Menu._fields['site'].ondelete, 'CASCADE')
        self.assertEqual(Menu._fields['uri'].ondelete, 'SET NULL')
        self.assertEqual(Menu._fields['element'].ondelete, 'SET NULL')
        self.assertEqual(Site._fields['header'].ondelete, 'SET NULL')
        self.assertEqual(Site._fields['footer'].ondelete, 'SET NULL')
        self.assertEqual(Site._fields['layout'].ondelete, 'SET NULL')
        self.assertEqual(SiteLang._fields['site'].ondelete, 'CASCADE')

    @with_transaction()
    def test_render_with_site_layout_keeps_content_without_chrome(self):
        Element = Pool().get('www.element')

        class DummyElement:
            @classmethod
            def get_element_kwargs(
                    cls, model, schema=None, show_preview_fields=True):
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
            def get_element_kwargs(
                    cls, model, schema=None, show_preview_fields=True):
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

    @with_transaction()
    def test_element_get_preview_returns_empty_when_page_is_missing(self):
        Element = Pool().get('www.element')

        element = Element()
        element.page = None
        element.model = SimpleNamespace(name='test.component')
        element.schema = None
        element.show_preview_fields = True

        preview = element.get_preview()

        self.assertEqual(preview, b'')

del ModuleTestCase
