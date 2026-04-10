import inspect
import re
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from dominate.tags import div
from trytond.exceptions import UserError
from trytond.i18n import gettext as _
from trytond.model import ModelSQL, ModelView, fields, sequence_ordered
from trytond.modules.voyager.voyager import Endpoint, VoyagerContext
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.tools import slugify
from trytond.transaction import Transaction
from trytond.url import http_host

LANGS = ['es', 'en', 'ca']


class Page(ModelSQL, ModelView):
    __name__ = 'www.page'

    name = fields.Char('Name', required=True)
    site = fields.Many2One('www.site', 'Site', required=True)
    uri_es = fields.Char('URI ES')
    main_uri_es = fields.Boolean('Main URI ES')
    uri_en = fields.Char('URI EN')
    main_uri_en = fields.Boolean('Main URI EN')
    uri_ca = fields.Char('URI CA')
    main_uri_ca = fields.Boolean('Main URI CA')
    element = fields.One2Many(
        'www.element', 'page', 'Elements',
        order=[('sequence', 'ASC')],
    )
    preview = fields.Function(
        fields.Binary('Page Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

    @staticmethod
    def _uris_from_name(name):
        if not name:
            return (None, None, None)
        base = slugify(name)
        if base:
            base = base.lower()
        if not base:
            return (None, None, None)
        return (f'/es/{base}', f'/en/{base}', f'/ca/{base}')

    @classmethod
    def _fill_uri_fields(cls, values, name):
        uri_es, uri_en, uri_ca = cls._uris_from_name(name)
        for code, uri_value in zip(LANGS, (uri_es, uri_en, uri_ca)):
            field_name = f'uri_{code}'
            if uri_value and not values.get(field_name):
                values[field_name] = uri_value

    @classmethod
    def create(cls, vlist):
        vlist = [values.copy() for values in vlist]
        for values in vlist:
            if values.get('name'):
                cls._fill_uri_fields(values, values['name'])
        return super().create(vlist)

    @classmethod
    def write(cls, pages, values, *args):
        values = values.copy()
        if values.get('name'):
            cls._fill_uri_fields(values, values['name'])
        return super().write(pages, values, *args)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
            'generate_uri': {},
        })

    @classmethod
    def validate(cls, pages):
        super().validate(pages)
        for page in pages:
            page.check_main_uri()

    def check_main_uri(self):
        if sum((
            bool(self.main_uri_es),
            bool(self.main_uri_en),
            bool(self.main_uri_ca),
        )) > 1:
            raise UserError(
                _('nantic.msg_page_main_uri_unique',
                    page=self.rec_name)
            )

    @classmethod
    @ModelView.button
    def generate_uri(cls, pages):
        pool = Pool()
        URI = pool.get('www.uri')
        Lang = pool.get('ir.lang')
        Model = pool.get('ir.model')

        endpoint_model = Model.search(
            [('name', '=', 'www.content.wrapper')],
            limit=1
        )
        if not endpoint_model:
            raise UserError(
                _('nantic.msg_page_generate_uri_missing_endpoint')
            )

        endpoint = endpoint_model[0]

        languages = {
            lang.code: lang
            for lang in Lang.search([('code', 'in', LANGS)])
        }

        for page in pages:
            resource_ref = f'{page.__name__},{page.id}'

            existing_uris = {
                (uri.uri, uri.language.code if uri.language else None): uri
                for uri in URI.search([
                    ('resource', '=', resource_ref),
                    ('site', '=', page.site.id),
                ])
            }

            new_uris = []
            main_code = None

            for code in LANGS:
                if getattr(page, f'main_uri_{code}'):
                    main_code = code

            uri_by_code = {}

            for code in LANGS:
                uri_value = getattr(page, f'uri_{code}')
                if not uri_value:
                    continue

                lang = languages.get(code)
                key = (uri_value, code)

                if key in existing_uris:
                    uri = existing_uris.pop(key)
                else:
                    uri = URI()
                    uri.resource = resource_ref

                uri.site = page.site
                uri.uri = uri_value
                uri.language = lang
                uri.endpoint = endpoint

                new_uris.append(uri)
                uri_by_code[code] = uri

            main_uri = uri_by_code.get(main_code) if main_code else None

            for uri in new_uris:
                if uri is main_uri:
                    uri.main_uri = None
                else:
                    uri.main_uri = main_uri

            if new_uris:
                URI.save(new_uris)

            if existing_uris:
                URI.delete(list(existing_uris.values()))

    @staticmethod
    def _preview_message(message):
        return Element._build_preview_document(
            '<div style="padding: 1rem; color: #666; font-family: sans-serif;">'
            f'{escape(message)}'
            '</div>'
        )

    @classmethod
    def render_preview_content(cls, page):
        pool = Pool()
        Wrapper = pool.get('www.content.wrapper')
        site = page.site if page and page.site else None
        voyager_context = Transaction().context.get('voyager_context')
        if voyager_context:
            voyager_context = VoyagerContext(
                site=site or getattr(voyager_context, 'site', None),
                session=getattr(voyager_context, 'session', None),
                cache=getattr(voyager_context, 'cache', None),
                request=getattr(voyager_context, 'request', None),
                adapter=getattr(voyager_context, 'adapter', None),
                endpoint_args=getattr(voyager_context, 'endpoint_args', None),
                web_prefix=getattr(voyager_context, 'web_prefix', None),
            )
        else:
            voyager_context = VoyagerContext(site=site)
        with Transaction().set_context(
                voyager_context=voyager_context,
                voyager_cms_preview=True):
            rendered = Wrapper(page=page).render()
        if hasattr(rendered, 'render'):
            content = rendered.render()
        else:
            content = str(rendered or '')
        if not (content or '').strip():
            return cls._preview_message('No preview available.')
        return Element._ensure_preview_document(content, site=site)

    @fields.depends('site', 'element', 'name')
    def get_preview(self, name=None):
        if not self.site:
            return self._preview_message(
                'Select a site to preview the page.'
            ).encode()
        try:
            return self.render_preview_content(self).encode()
        except Exception as exc:
            return Element._build_preview_document(
                '<div style="padding: 1rem; font-family: monospace; '
                'white-space: pre-wrap; color: #b91c1c;">'
                f'{escape(str(exc) or "Preview not available.")}'
                '</div>'
            ).encode()

    @fields.depends('id')
    def get_preview_filename(self, name=None):
        return f'page-preview-{self.id or "new"}.html'


class Element(sequence_ordered(), ModelSQL, ModelView):
    __name__ = 'www.element'
    _table = 'www_component'

    name = fields.Char('Name', required=True)
    model = fields.Many2One('ir.model', 'Model', required=True)
    page = fields.Many2One('www.page', 'Page')
    schema = fields.One2Many('www.schema', 'component', "Schema",
        size=1, add_remove=[('component', '=', None)])
    preview = fields.Function(
        fields.Binary('HTML Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

    @classmethod
    def _preview_image(cls):
        return (
            'data:image/svg+xml,'
            '%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 '
            'viewBox=%220 0 1200 800%22%3E'
            '%3Crect width=%221200%22 height=%22800%22 fill=%22%23e2e8f0%22/%3E'
            '%3Ctext x=%22600%22 y=%22400%22 text-anchor=%22middle%22 '
            'font-family=%22sans-serif%22 font-size=%2264%22 '
            'fill=%22%23475569%22%3EPreview%3C/text%3E%3C/svg%3E'
        )

    @classmethod
    def _preview_text_value(cls, field_name, field):
        field_name = field_name.lower()
        string = getattr(field, 'string', '') or field_name.replace('_', ' ')
        if 'title' in field_name:
            return f'Preview {string}'.strip()
        if 'subtitle' in field_name:
            return f'Sample {string}'.strip()
        if field_name.startswith('text') or 'description' in field_name:
            return (
                f'Preview content for {string}. This placeholder is shown '
                'until a schema with real content is assigned.'
            )
        if 'alt' in field_name:
            return 'Preview image'
        if 'url' in field_name or 'href' in field_name or 'link' in field_name:
            return '#'
        if 'icon' in field_name:
            return 'M12 4v16m8-8H4'
        if 'color' in field_name:
            return 'bg-slate-50'
        return f'Preview {string}'.strip()

    @classmethod
    def _preview_selection_value(cls, field_name, field):
        options = getattr(field, 'selection', None) or []
        preferred_by_name = {
            'background_hue': 'slate',
            'background_color': 'bg-slate-50',
        }
        preferred = preferred_by_name.get(field_name)
        values = [value for value, _label in options if value not in (None, '')]
        if preferred in values:
            return preferred
        return values[0] if values else None

    @classmethod
    def _preview_value_for_field(cls, field_name, field):
        field_name = field_name.lower()
        integer_types = tuple(t for t in [
            getattr(fields, 'Integer', None),
            getattr(fields, 'BigInteger', None),
        ] if t)
        numeric_types = tuple(t for t in [
            getattr(fields, 'Float', None),
            getattr(fields, 'Numeric', None),
        ] if t)

        if isinstance(field, fields.Many2One):
            if field.model_name == 'www.menu':
                Menu = Pool().get('www.menu')
                menu = Menu()
                menu.name = 'Preview Menu'
                menu.menus = []
                return menu
            return None

        if isinstance(field, fields.Selection):
            return cls._preview_selection_value(field_name, field)
        if isinstance(field, fields.Date):
            return date.today()
        if isinstance(field, fields.Boolean):
            return True
        if integer_types and isinstance(field, integer_types):
            return 3
        if numeric_types and isinstance(field, numeric_types):
            return 3
        if isinstance(field, (fields.Char, fields.Text)):
            if 'image' in field_name and (
                    'url' in field_name or 'src' in field_name):
                return cls._preview_image()
            if field_name == 'items_json':
                return '[]'
            return cls._preview_text_value(field_name, field)
        return None

    @classmethod
    def _build_preview_schema(cls, include_visual=True):
        pool = Pool()
        Schema = pool.get('www.schema')

        schema = Schema()
        for field_name, field in Schema._fields.items():
            if field_name in {
                    'component', 'id', 'create_uid', 'create_date',
                    'write_uid', 'write_date', 'rec_name', 'model_name'}:
                continue
            lower_name = field_name.lower()
            if not include_visual and (
                    'color' in lower_name
                    or 'hue' in lower_name
                    or ('image' in lower_name and (
                        'url' in lower_name or 'src' in lower_name))):
                continue
            value = cls._preview_value_for_field(field_name, field)
            if value is not None:
                setattr(schema, field_name, value)
        return schema

    @classmethod
    def _build_preview_schema_with_values(cls, schema):
        preview_schema = cls._build_preview_schema(include_visual=bool(schema))
        if not schema:
            return preview_schema

        for field_name in getattr(preview_schema, '_fields', {}):
            if field_name in {'id', 'create_uid', 'create_date',
                    'write_uid', 'write_date', 'rec_name', 'model_name'}:
                continue
            if not hasattr(schema, field_name):
                continue
            value = getattr(schema, field_name)
            if value is None:
                continue
            if isinstance(value, str) and value == '':
                continue
            if isinstance(value, (list, tuple)) and not value:
                continue
            setattr(preview_schema, field_name, value)
        return preview_schema

    @classmethod
    def get_element_schema(cls, model, schema=None):
        if isinstance(schema, (list, tuple)):
            schema = next(
                (item for item in schema if getattr(item, 'id', None)),
                schema[0] if schema else None)
        if schema and not getattr(schema, 'id', None):
            has_meaningful_value = any(
                getattr(schema, fname, None) not in (None, '', [], ())
                for fname in getattr(schema, '_fields', {})
                if fname not in {
                    'id', 'component', 'create_uid', 'create_date',
                    'write_uid', 'write_date', 'rec_name', 'model_name'}
            )
            if not has_meaningful_value:
                schema = None
        if (
                Transaction().context.get('voyager_cms_preview')
                and 'schema' in getattr(model, '_fields', {})):
            return cls._build_preview_schema_with_values(schema)
        if schema:
            return schema
        return None

    @classmethod
    def get_element_kwargs(cls, model, schema=None):
        schema = cls.get_element_schema(model, schema)
        if not schema:
            return {}

        kwargs = {}
        if 'schema' in model._fields:
            kwargs['schema'] = schema
        return kwargs

    @classmethod
    def _preview_asset_models(cls, site=None, model_name=None):
        pool = Pool()
        models = []
        seen = set()

        for name in filter(None, [
                    getattr(
                        getattr(getattr(site, 'layout', None), 'model', None),
                        'name', None),
                    getattr(
                        getattr(getattr(site, 'header', None), 'model', None),
                        'name', None),
                    getattr(
                        getattr(getattr(site, 'footer', None), 'model', None),
                        'name', None),
                    model_name]):
            if name in seen:
                continue
            seen.add(name)
            models.append(pool.get(name))
        return models

    @staticmethod
    def _preview_module_root(model):
        try:
            module_path = Path(inspect.getfile(model)).resolve()
        except (OSError, TypeError):
            return None

        for path in module_path.parents:
            if path.parent.name == 'modules':
                return path
        return None

    @classmethod
    def _resolve_preview_asset_path(cls, model, asset_path):
        path = Path(asset_path)
        if path.is_absolute():
            return path

        module_root = cls._preview_module_root(model)
        if not module_root:
            return None
        return module_root / path

    @staticmethod
    def _uses_tailwind_cdn(content):
        return 'cdn.tailwindcss.com' in (content or '')

    @classmethod
    def _module_output_css_paths(cls, model):
        module_root = cls._preview_module_root(model)
        if not module_root:
            return []
        return sorted(module_root.rglob('output.css'))

    @classmethod
    def _build_preview_document(
            cls, content, extra_head='', site=None, model_name=None):
        base_url = http_host()
        base_styles = ''
        # Inject Tailwind only for standalone element previews.
        if not site:
            base_styles = (
                '<link rel="stylesheet" '
                'href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css"/>'
            )
        return (
            '<!DOCTYPE html>'
            '<html lang="ca">'
            '<head>'
            '<meta charset="utf-8"/>'
            '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
            f'<base href="{base_url}"/>'
            f'{extra_head}'
            f'{base_styles}'
            '</head>'
            '<body>'
            f'{content}'
            '</body>'
            '</html>'
        )

    @classmethod
    def _ensure_preview_document(cls, content, site=None, model_name=None):
        lower = content[:500].lower()
        if (
                content.lstrip().lower().startswith('<!doctype html>')
                or '<html' in lower):
            return cls._build_preview_document(
                content, site=site, model_name=model_name)
        return cls._build_preview_document(
            content, site=site, model_name=model_name)

    @classmethod
    def render_with_site_layout(cls, site, content, title):
        if not site:
            return content

        pool = Pool()
        layout_component = getattr(site, 'layout', None)
        layout = None
        if layout_component and layout_component.model:
            LayoutModel = pool.get(layout_component.model.name)
            layout = LayoutModel(
                **cls.get_element_kwargs(
                    LayoutModel, layout_component.schema))
        if layout is None:
            return content
        voyager_context = Transaction().context.get('voyager_context')
        if voyager_context:
            voyager_context = VoyagerContext(
                site=site,
                session=getattr(voyager_context, 'session', None),
                cache=getattr(voyager_context, 'cache', None),
                request=getattr(voyager_context, 'request', None),
                adapter=getattr(voyager_context, 'adapter', None),
                endpoint_args=getattr(voyager_context, 'endpoint_args', None),
                web_prefix=getattr(voyager_context, 'web_prefix', None),
            )
        else:
            voyager_context = VoyagerContext(site=site)
        with Transaction().set_context(voyager_context=voyager_context):
            try:
                return layout.render(content=content, title=title)
            except TypeError as exc:
                if "unexpected keyword argument 'content'" not in str(exc):
                    raise
                try:
                    return layout.render(content, title=title)
                except TypeError as nested_exc:
                    if "unexpected keyword argument 'title'" not in str(nested_exc):
                        raise
                    if hasattr(layout, 'main'):
                        layout.main.add(content)
                    if hasattr(layout, 'title'):
                        layout.title = title
                    return layout.render()

    @classmethod
    def render_element_content(cls, model_name, schema=None):
        pool = Pool()
        ElementModel = pool.get(model_name)
        with div() as content:
            tag = ElementModel(
                **cls.get_element_kwargs(ElementModel, schema)
            ).tag()
            if tag is not None and not getattr(content, 'children', None):
                content.add(tag)
        return content

    @classmethod
    def render_preview_content(cls, element):
        site = element.page.site if element.page and element.page.site else None
        voyager_context = Transaction().context.get('voyager_context')
        if voyager_context:
            voyager_context = VoyagerContext(
                site=site or getattr(voyager_context, 'site', None),
                session=getattr(voyager_context, 'session', None),
                cache=getattr(voyager_context, 'cache', None),
                request=getattr(voyager_context, 'request', None),
                adapter=getattr(voyager_context, 'adapter', None),
                endpoint_args=getattr(voyager_context, 'endpoint_args', None),
                web_prefix=getattr(voyager_context, 'web_prefix', None),
            )
        else:
            voyager_context = VoyagerContext(site=site)
        with Transaction().set_context(
                voyager_context=voyager_context,
                voyager_cms_preview=True):
            rendered = cls.render_element_content(
                element.model.name, element.schema)
            if site:
                rendered = cls.render_with_site_layout(
                    site, rendered, element.page.name or element.name)
        if hasattr(rendered, 'render'):
            content = rendered.render()
        else:
            content = str(rendered or '')
        if not (content or '').strip():
            return cls._build_preview_document(
                '<div style="padding: 1rem; color: #cbd5e1; '
                'font-family: sans-serif;">'
                'No preview available.'
                '</div>',
                site=site,
                model_name=element.model.name)
        return cls._ensure_preview_document(
            content, site=site, model_name=element.model.name)

    @fields.depends('model', 'schema')
    def get_preview(self, name=None):
        try:
            content = self.render_preview_content(self)
        except Exception as exc:
            content = (
                '<div style="padding: 1rem; font-family: monospace; '
                'white-space: pre-wrap; color: #fca5a5;">'
                f'{escape(str(exc) or "Preview not available.")}'
                '</div>'
            )
            content = self._build_preview_document(
                content,
                site=self.page.site if self.page and self.page.site else None,
                model_name=self.model.name)
        return content.encode()

    @fields.depends('id')
    def get_preview_filename(self, name=None):
        return f'element-preview-{self.id or "new"}.html'


class Component(Element):
    __name__ = 'www.component'
    _table = 'www_component'


class Schema(ModelSQL, ModelView):
    __name__ = 'www.schema'

    component = fields.Many2One('www.element', 'Element')
    model_name = fields.Function(fields.Char('Model Name'),
        'on_change_with_model_name')

    @fields.depends('component', '_parent_component.model')
    def on_change_with_model_name(self, name=None):
        if self.component and self.component.model:
            return self.component.model.name
        return None


class ContentWrapper(Endpoint):
    __name__ = 'www.content.wrapper'
    _url = '/content-wrapper'
    _type = 'www'
    page = fields.Many2One('www.page', 'Page')

    def get_not_found_content(self):
        with div() as content:
            content.add(div(_("Page not found")))
        return content

    def get_not_found_title(self):
        return _("Page not found")

    def render(self):
        pool = Pool()
        Element = pool.get('www.element')
        layout_component = self.site.layout
        layout = None
        if layout_component and layout_component.model:
            LayoutModel = pool.get(layout_component.model.name)
            layout = LayoutModel(
                **Element.get_element_kwargs(
                    LayoutModel, layout_component.schema))

        def _render_layout(content, title):
            if layout is None:
                return content.render()
            try:
                return layout.render(content=content, title=title)
            except TypeError as exc:
                if "unexpected keyword argument 'content'" not in str(exc):
                    raise
                try:
                    return layout.render(content, title=title)
                except TypeError as nested_exc:
                    if "unexpected keyword argument 'title'" not in str(nested_exc):
                        raise
                    if hasattr(layout, 'main'):
                        layout.main.add(content)
                    if hasattr(layout, 'title'):
                        layout.title = title
                    return layout.render()

        if not self.page:
            return _render_layout(
                content=self.get_not_found_content(),
                title=self.get_not_found_title(),
            )

        with div() as page_content:
            for element in self.page.element:
                ElementModel = pool.get(element.model.name)
                ElementModel(
                    **Element.get_element_kwargs(
                        ElementModel, element.schema)
                ).tag()

        return _render_layout(content=page_content, title=self.page.name)


class VoyagerURI(metaclass=PoolMeta):
    __name__ = 'www.uri'

    @classmethod
    def _get_resources(cls):
        return super()._get_resources() + ['www.page']


class VoyagerMenu(metaclass=PoolMeta):
    __name__ = 'www.menu'

    component = fields.Many2One('www.element', 'Element',
        states={
            'invisible': Eval('type') != 'component',
        },
        depends=['type'])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.type.selection.append(('component', 'Element'))


class VoyagerSite(metaclass=PoolMeta):
    __name__ = 'www.site'

    header = fields.Many2One('www.element', 'Header')
    footer = fields.Many2One('www.element', 'Footer')
    layout = fields.Many2One('www.element', 'Layout')
