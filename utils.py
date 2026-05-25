from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from dominate.tags import div
from dominate.util import raw
from trytond.exceptions import UserError
from trytond.i18n import gettext as _
from slugify import slugify

from trytond.model import ModelSQL, ModelView, fields, sequence_ordered
from trytond.pool import Pool, PoolMeta
from trytond.exceptions import UserError
from trytond.i18n import gettext
from trytond.modules.voyager.voyager import Endpoint, VoyagerContext
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.url import http_host
from werkzeug.wrappers import Response

LANGS = ['es', 'en', 'ca']

DEFAULT_BACKGROUND_COLOR = '#F8FAFC'


class Page(ModelSQL, ModelView):
    __name__ = 'www.page'

    name = fields.Char('Name', required=True)
    site = fields.Many2One('www.site', 'Site', required=True)
    uris = fields.One2Many(
        'www.page.uri', 'page', 'URIs',
        order=[('id', 'ASC')],
    )
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

    @classmethod
    def _uri_from_name(cls, name, code):
        if not name:
            return None
        base = name.lower().replace(' ', '-').replace('/', '-')
        if not base:
            return None
        return f'/{code}/{base}'

    @classmethod
    def _default_uris(cls, name, site=None):
        langs = LANGS
        if site and hasattr(site, 'id') and site.id:
            try:
                pool = Pool()
                SiteLang = pool.get('www.site.lang')
                site_langs = SiteLang.search([('site', '=', site.id)])
                if site_langs:
                    langs = [sl.language.code for sl in site_langs]
            except Exception:
                pass
        pool = Pool()
        Lang = pool.get('ir.lang')
        languages = {
            lang.code: lang
            for lang in Lang.search([('code', 'in', langs)])
        }
        uris = []
        for code in langs:
            language = languages.get(code)
            if not language:
                continue
            uris.append({
                'language': language.id,
                'uri': cls._uri_from_name(name, code),
            })
        return uris

    @classmethod
    def _fill_uris(cls, uris, name, site=None):
        langs = LANGS
        if site and hasattr(site, 'id') and site.id:
            try:
                pool = Pool()
                SiteLang = pool.get('www.site.lang')
                site_langs = SiteLang.search([('site', '=', site.id)])
                if site_langs:
                    langs = [sl.language.code for sl in site_langs]
            except Exception:
                pass
        changed = []
        for uri in uris or []:
            language = getattr(uri, 'language', None)
            if not language or uri.uri:
                continue
            if language.code not in langs:
                continue
            uri.uri = cls._uri_from_name(name, language.code)
            changed.append(uri)
        return changed

    @fields.depends('name', 'site')
    def on_change_name(self):
        if not self.uris:
            self.uris = self._default_uris(self.name, self.site)
        else:
            self._fill_uris(self.uris, self.name, self.site)

    @classmethod
    def create(cls, vlist):
        vlist = [values.copy() for values in vlist]
        pages = super().create(vlist)
        cls._create_default_uris(pages)
        return pages

    @classmethod
    def _create_default_uris(cls, pages):
        if not pages:
            return
        pool = Pool()
        PageURI = pool.get('www.page.uri')
        to_create = []
        for page in pages:
            if page.uris:
                continue
            to_create.extend({
                'page': page.id,
                'language': uri.language.id,
                'uri': uri.uri,
            } for uri in cls._default_uris(page.name, page.site))
        if to_create:
            PageURI.create(to_create)

    @classmethod
    def write(cls, pages, values, *args):
        values = values.copy()
        super().write(pages, values, *args)
        pages_without_uris = [page for page in pages if not page.uris]
        if pages_without_uris:
            cls._create_default_uris(pages_without_uris)
        if 'name' in values:
            pool = Pool()
            PageURI = pool.get('www.page.uri')
            changed = []
            for page in pages:
                changed.extend(cls._fill_uris(page.uris, page.name, page.site))
            if changed:
                PageURI.save(changed)

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
        if sum(bool(uri.main_uri) for uri in self.uris) > 1:
            raise UserError(
                gettext('voyager_cms.msg_page_main_uri_unique',
                  page=self.rec_name)
            )

    @classmethod
    @ModelView.button
    def generate_uri(cls, pages):
        pool = Pool()
        URI = pool.get('www.uri')
        Model = pool.get('ir.model')
        SiteLang = pool.get('www.site.lang')

        endpoint_model = Model.search(
            [('name', '=', 'www.content.wrapper')],
            limit=1
        )
        if not endpoint_model:
            raise UserError(
                gettext('voyager_cms.msg_page_generate_uri_missing_endpoint')
            )

        endpoint = endpoint_model[0]

        for page in pages:
            if not page.site:
                raise UserError(
                    gettext('voyager_cms.msg_page_generate_uri_missing_site',
                      page=page.rec_name)
                )
            resource_ref = f'{page.__name__},{page.id}'

            if not page.uris:
                cls._create_default_uris([page])
                page = cls.search([('id', '=', page.id)], limit=1)[0]

            changed = cls._fill_uris(page.uris, page.name, page.site)
            if changed:
                pool.get('www.page.uri').save(changed)

            existing_uris = {
                (uri.uri, uri.language.code if uri.language else None): uri
                for uri in URI.search([
                    ('resource', '=', resource_ref),
                    ('site', '=', page.site.id),
                ])
            }

            new_uris = []
            main_uri = None

            for uri_row in page.uris:
                if not uri_row.uri or not uri_row.language:
                    continue
                code = uri_row.language.code
                site_langs = LANGS
                if page.site and hasattr(page.site, 'id') and page.site.id:
                    try:
                        site_lang_records = SiteLang.search([('site', '=', page.site.id)])
                        if site_lang_records:
                            site_langs = [sl.language.code for sl in site_lang_records]
                    except Exception:
                        pass
                if code not in site_langs:
                    continue

                key = (uri_row.uri, code)

                if key in existing_uris:
                    uri = existing_uris.pop(key)
                else:
                    uri = URI()
                    uri.resource = resource_ref

                uri.site = page.site
                uri.uri = uri_row.uri
                uri.language = uri_row.language
                uri.endpoint = endpoint

                new_uris.append(uri)
                if uri_row.main_uri:
                    main_uri = uri

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


class PageURI(ModelSQL, ModelView):
    __name__ = 'www.page.uri'

    page = fields.Many2One('www.page', 'Page', required=True, ondelete='CASCADE')
    language = fields.Many2One('ir.lang', 'Idioma', required=True)
    uri = fields.Char('URI')
    main_uri = fields.Boolean('Main URI')

    @fields.depends('page', 'language', 'uri', '_parent_page.name')
    def on_change_language(self):
        if self.uri or not self.language:
            return
        name = None
        if self.page and self.page.name:
            name = self.page.name
        if not name:
            return
        self.uri = Page._uri_from_name(name, self.language.code)

    @fields.depends('page', 'main_uri')
    def on_change_main_uri(self):
        if not self.main_uri or not self.page or not self.page.uris:
            return
        for uri in self.page.uris:
            if uri is not self:
                uri.main_uri = False

    def get_rec_name(self, name):
        language = self.language.code if self.language else ''
        return f'{language}: {self.uri or ""}'.strip(': ')  


class Element(sequence_ordered(), ModelSQL, ModelView):
    __name__ = 'www.element'
    _table = 'www_component'

    name = fields.Char('Name', required=True)
    component_models = fields.Function(
        fields.Many2Many('ir.model', None, None, 'Component Models'),
        'get_component_models')
    model = fields.Many2One(
        'ir.model', 'Model', required=True,
        domain=[('id', 'in', Eval('component_models'))],
        depends=['component_models'])
    page = fields.Many2One('www.page', 'Page')
    schema = fields.One2Many('www.schema', 'component', "Schema",
        size=1, add_remove=[('component', '=', None)])
    show_preview = fields.Boolean('Show Preview')
    preview = fields.Function(
        fields.Binary('HTML Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

    @classmethod
    def default_show_preview(cls):
        return True

    @classmethod
    def _component_model_names(cls):
        """
        Return the list of Tryton model names that are valid CMS components.

        We detect components dynamically by checking if instantiating the model
        yields an instance of the Voyager Component base class (or compatible).
        """
        pool = Pool()
        try:
            from trytond.modules.voyager.voyager import Component as ComponentCMS
        except Exception:  # pragma: no cover
            ComponentCMS = None

        names = []
        for model_name, model_cls in pool.iterobject(type='model'):
            if not model_name:
                continue
            if model_name.startswith('ir.'):
                continue
            if not model_cls:
                continue
            if ComponentCMS is None:
                continue
            try:
                instance = model_cls(render=False)
            except Exception:
                try:
                    instance = model_cls()
                except Exception:
                    continue
            if isinstance(instance, ComponentCMS):
                names.append(model_name)
        return names

    @classmethod
    def _get_component_model_ids(cls):
        pool = Pool()
        Model = pool.get('ir.model')
        names = cls._component_model_names()
        if not names:
            return []
        return [m.id for m in Model.search([('name', 'in', names)])]

    @classmethod
    def get_component_models(cls, elements, name):
        model_ids = cls._get_component_model_ids()
        return {element.id: model_ids for element in elements}

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
            return DEFAULT_BACKGROUND_COLOR
        return f'Preview {string}'.strip()

    @classmethod
    def _preview_selection_value(cls, field_name, field):
        options = getattr(field, 'selection', None) or []
        if isinstance(options, str):
            selection_getter = getattr(cls, options, None)
            if callable(selection_getter):
                try:
                    options = selection_getter()
                except TypeError:
                    options = []
        values = []
        for option in options or []:
            value = option[0] if isinstance(option, (list, tuple)) else option
            if value not in (None, ''):
                values.append(value)
        return values[0] if values else ''

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
        provided_schema = schema is not None
        if isinstance(schema, (list, tuple)):
            schema = next(
                (item for item in schema if getattr(item, 'id', None)),
                schema[0] if schema else None)
        if (
                Transaction().context.get('voyager_cms_preview')
                and 'schema' in getattr(model, '_fields', {})):
            if schema:
                return schema
            return cls._build_preview_schema()
        if provided_schema:
            return schema
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
    def render_with_site_layout(
            cls, site, content, title, preview_chrome=False):
        if not site:
            return content

        pool = Pool()
        Element = pool.get('www.element')
        layout_component = getattr(site, 'layout', None)
        layout = None
        if layout_component and layout_component.model:
            LayoutModel = pool.get(layout_component.model.name)
            layout = LayoutModel(
                **cls.get_element_kwargs(
                    LayoutModel, layout_component.schema))
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
            if site and not preview_chrome:
                with div() as wrapped:
                    header_component = getattr(site, 'header', None)
                    if header_component and header_component.model:
                        try:
                            HeaderModel = pool.get(header_component.model.name)
                            HeaderModel(**Element.get_element_kwargs(
                                HeaderModel, header_component.schema)).tag()
                        except Exception:
                            pass

                    if content:
                        if hasattr(content, 'render'):
                            wrapped.add(content)
                        else:
                            raw(str(content))

                    footer_component = getattr(site, 'footer', None)
                    if footer_component and footer_component.model:
                        try:
                            FooterModel = pool.get(footer_component.model.name)
                            FooterModel(**Element.get_element_kwargs(
                                FooterModel, footer_component.schema)).tag()
                        except Exception:
                            try:
                                pool.get('www.footer')().tag()
                            except Exception:
                                pass
                    else:
                        try:
                            pool.get('www.footer')().tag()
                        except Exception:
                            pass
                content = wrapped
            if layout is None:
                return content
            try:
                rendered = layout.render(content=content, title=title)
            except TypeError as exc:
                if "unexpected keyword argument 'content'" not in str(exc):
                    raise
                try:
                    rendered = layout.render(content, title=title)
                except TypeError as nested_exc:
                    if "unexpected keyword argument 'title'" not in str(nested_exc):
                        raise
                    if hasattr(layout, 'main'):
                        layout.main.add(content)
                    if hasattr(layout, 'title'):
                        layout.title = title
                    rendered = layout.render()
        return rendered

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
                # element sense header footer
                rendered = cls.render_with_site_layout(
                    site, rendered, element.page.name or element.name,
                    preview_chrome=True)
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


class Schema(ModelSQL, ModelView):
    __name__ = 'www.schema'

    component = fields.Many2One('www.element', 'Element')
    icon = fields.Char('Icon')
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
            if self.site:
                with div() as wrapped:
                    header_component = getattr(self.site, 'header', None)
                    if header_component and header_component.model:
                        try:
                            HeaderModel = pool.get(header_component.model.name)
                            HeaderModel(**Element.get_element_kwargs(
                                HeaderModel, header_component.schema)).tag()
                        except Exception:
                            pass

                    if content:
                        if hasattr(content, 'render'):
                            wrapped.add(content)
                        else:
                            raw(str(content))

                    footer_component = getattr(self.site, 'footer', None)
                    if footer_component and footer_component.model:
                        try:
                            FooterModel = pool.get(footer_component.model.name)
                            FooterModel(**Element.get_element_kwargs(
                                FooterModel, footer_component.schema)).tag()
                        except Exception:
                            try:
                                pool.get('www.footer')().tag()
                            except Exception:
                                pass
                    else:
                        try:
                            pool.get('www.footer')().tag()
                        except Exception:
                            pass
                content = wrapped
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


class SiteLang(ModelSQL):
    __name__ = 'www.site.lang'

    site = fields.Many2One('www.site', 'Site', required=True)
    language = fields.Many2One('ir.lang', 'Language', required=True)


class VoyagerSite(metaclass=PoolMeta):
    __name__ = 'www.site'

    header = fields.Many2One('www.element', 'Header')
    footer = fields.Many2One('www.element', 'Footer')
    layout = fields.Many2One('www.element', 'Layout')
    langs = fields.Many2Many('www.site.lang', 'site', 'language', 'Languages')
