from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from dominate.tags import div
from dominate.util import raw
from trytond.exceptions import UserError
from trytond.i18n import gettext as _

from trytond.model import ModelSQL, ModelView, Workflow, fields, sequence_ordered
from trytond.pool import Pool, PoolMeta
from trytond.exceptions import UserError
from trytond.i18n import gettext
from trytond.modules.voyager.voyager import Component, Endpoint, VoyagerContext
from trytond.pyson import Eval
from trytond.tools import slugify
from trytond.transaction import Transaction
from trytond.url import http_host
from werkzeug.wrappers import Response

LANGS = ['es', 'en', 'ca']

DEFAULT_BACKGROUND_COLOR = '#F8FAFC'
_PAGE_STATES = {'readonly': Eval('state') != 'draft'}
_PAGE_DEPENDS = ['state']
_CHILD_PAGE_STATES = {'readonly': Eval('_parent_page', {}).get('state') != 'draft'}
_CHILD_PAGE_DEPENDS = ['page', '_parent_page.state']
CHILD_PAGES_STATES = {'readonly': Eval('page_state') != 'draft'}
CHILD_PAGES_DEPENDS = ['page_state']
CHILD_PAGES_DEFENDS = CHILD_PAGES_DEPENDS


class Page(Workflow, ModelSQL, ModelView):
    __name__ = 'www.page'

    name = fields.Char('Name', required=True,
        states=_PAGE_STATES, depends=_PAGE_DEPENDS)
    site = fields.Many2One('www.site', 'Site', required=True,
        ondelete='CASCADE',
        states=_PAGE_STATES, depends=_PAGE_DEPENDS)
    # links a published page back to its draft
    origin_page = fields.Many2One(
        'www.page', 'Origin Page', readonly=True, ondelete='SET NULL')
    # links a draft page to its current published copy
    published_page = fields.Many2One(
        'www.page', 'Published Page', readonly=True, ondelete='SET NULL')
    # current workflow state of the page
    state = fields.Selection([
            ('draft', 'Draft'),
            ('published', 'Published'),
            ], 'State', readonly=True, required=True, sort=False)
    uris = fields.One2Many(
        'www.page.uri', 'page', 'URIs',
        order=[('id', 'ASC')],
        states=_PAGE_STATES, depends=_PAGE_DEPENDS,
    )
    element = fields.One2Many(
        'www.element', 'page', 'Elements',
        order=[('sequence', 'ASC')],
        states=_PAGE_STATES, depends=_PAGE_DEPENDS,
    )
    preview = fields.Function(
        fields.Binary('Page Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

    @staticmethod
    def default_state():
        return 'draft'

    @classmethod
    def _delete_generated_uris(cls, pages):
        if not pages:
            return
        pool = Pool()
        URI = pool.get('www.uri')
        resources = [f'{page.__name__},{page.id}' for page in pages if page.id]
        if resources:
            uris = URI.search([
                    ('resource', 'in', resources),
                    ])
            if uris:
                URI.delete(uris)

    @classmethod
    def _find_published_pages_to_replace(cls, page):
        # finds the published copy that must be removed before publishing again
        pool = Pool()
        PageURI = pool.get('www.page.uri')
        if page.published_page:
            return [page.published_page]
        if not getattr(page, 'id', None):
            return []
        published_pages = cls.search([
                ('origin_page', '=', page.id),
                ('state', '=', 'published'),
                ])
        if published_pages:
            return published_pages
        target_uris = [
            uri['uri']
            for uri in cls._default_uris(page.name, page.site, state='published')
            if uri.get('uri')
            ]
        if not target_uris or not getattr(page, 'site', None):
            return []
        page_uris = PageURI.search([
                ('page.site', '=', page.site.id),
                ('page.state', '=', 'published'),
                ('page', '!=', page.id),
                ('uri', 'in', target_uris),
                ])
        page_ids = list({uri.page.id for uri in page_uris if getattr(uri, 'page', None)})
        if not page_ids:
            return []
        return cls.search([
                ('id', 'in', page_ids),
                ])

    @classmethod
    def _state_uri_prefix(cls, state):
        state = state or 'draft'
        if state == 'published':
            return ''
        return f'/{state}'

    @classmethod
    def _uri_from_name(cls, name, code, state='published'):
        if not name:
            return None
        base = name.lower().replace(' ', '-').replace('/', '-')
        if not base:
            return None
        prefix = cls._state_uri_prefix(state)
        return f'{prefix}/{code}/{base}'

    @classmethod
    def _default_uris(cls, name, site=None, state='published'):
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
                'uri': cls._uri_from_name(name, code, state=state),
            })
        return uris

    @classmethod
    def _fill_uris(cls, uris, name, site=None, state='published', force=False):
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
            if not language:
                continue
            if language.code not in langs:
                continue
            if uri.uri and not force:
                continue
            uri.uri = cls._uri_from_name(name, language.code, state=state)
            changed.append(uri)
        return changed

    @fields.depends('name', 'site', 'state', 'uris')
    def on_change_name(self):
        if not self.uris:
            self.uris = self._default_uris(
                self.name, self.site, state=self.state or 'draft')
        else:
            self._fill_uris(
                self.uris, self.name, self.site, state=self.state or 'draft')

    @classmethod
    def create(cls, vlist):
        vlist = [values.copy() for values in vlist]
        pages = super().create(vlist)
        cls._create_default_uris(pages)
        return pages

    @classmethod
    def delete(cls, pages):
        cls._delete_generated_uris(pages)
        super().delete(pages)

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
                'language': uri['language'],
                'uri': uri['uri'],
            } for uri in cls._default_uris(
                page.name, page.site, state=page.state or 'draft'))
        if to_create:
            PageURI.create(to_create)

    @classmethod
    def _sync_page_uris(cls, pages, force=False):
        pool = Pool()
        PageURI = pool.get('www.page.uri')
        changed = []
        for page in pages:
            changed.extend(cls._fill_uris(
                page.uris, page.name, page.site,
                state=page.state or 'draft', force=force))
        if changed:
            PageURI.save(changed)

    @classmethod
    def write(cls, pages, values, *args):
        values = values.copy()
        super().write(pages, values, *args)
        pages_without_uris = [page for page in pages if not page.uris]
        if pages_without_uris:
            cls._create_default_uris(pages_without_uris)
        if 'state' in values:
            cls._sync_page_uris(pages, force=True)
            cls.generate_uri(pages)
        elif 'name' in values:
            cls._sync_page_uris(pages)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._transitions |= set((
                ('draft', 'published'),
                ('published', 'draft'),
                ))
        cls._buttons.update({
            'generate_uri': {},
            'publish': {
                'invisible': Eval('state') != 'draft',
                'depends': ['state'],
                },
            'draft': {
                'invisible': Eval('state') != 'published',
                'depends': ['state'],
                },
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

            changed = cls._fill_uris(
                page.uris, page.name, page.site,
                state=page.state or 'draft')
            if changed:
                pool.get('www.page.uri').save(changed)

            existing_uris = {}
            existing_uris_by_language = {}
            for uri in URI.search([
                    ('resource', '=', resource_ref),
                    ('site', '=', page.site.id),
                ]):
                code = uri.language.code if uri.language else None
                existing_uris[(uri.uri, code)] = uri
                if code:
                    existing_uris_by_language[code] = uri

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
                    existing_uris_by_language.pop(code, None)
                elif code in existing_uris_by_language:
                    uri = existing_uris_by_language.pop(code)
                    existing_uris.pop(
                        (uri.uri, uri.language.code if uri.language else None),
                        None)
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

            conflicting_uris = []
            new_uri_ids = {uri.id for uri in new_uris if getattr(uri, 'id', None)}
            for uri in new_uris:
                duplicates = URI.search([
                        ('site', '=', page.site.id),
                        ('uri', '=', uri.uri),
                        ('id', 'not in', list(new_uri_ids) or [-1]),
                        ], limit=10)
                for duplicate in duplicates:
                    if duplicate.id not in {u.id for u in conflicting_uris}:
                        conflicting_uris.append(duplicate)
            if conflicting_uris:
                URI.delete(conflicting_uris)

            if new_uris:
                URI.save(new_uris)
            if existing_uris:
                URI.delete(list(existing_uris.values()))

    @classmethod
    @ModelView.button
    @Workflow.transition('published')
    def publish(cls, pages):
        # turns the current draft into the new published page
        for page in pages:
            old_published_pages = cls._find_published_pages_to_replace(page)
            if old_published_pages:
                cls._delete_generated_uris(old_published_pages)
                cls.delete(old_published_pages)
            cls.write([page], {
                    'origin_page': None,
                    'published_page': None,
                    })

    @classmethod
    def _freeze_published_copy(cls, page):
        # keeps a frozen published copy while the current page goes back to draft
        if page.origin_page and page.origin_page.state == 'draft':
            return page
        old_published_pages = cls._find_published_pages_to_replace(page)
        if old_published_pages:
            cls._delete_generated_uris(old_published_pages)
            cls.delete(old_published_pages)
        published_page, = cls.copy([page], default={
                'state': 'published',
                'origin_page': page.id,
                'published_page': None,
                })
        cls._sync_page_uris([published_page], force=True)
        cls.generate_uri([published_page])
        cls.write([page], {'published_page': published_page.id})
        return published_page

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, pages):
        # creates the frozen published copy and keeps editing on the same page
        for page in pages:
            cls._freeze_published_copy(page)

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

    page = fields.Many2One('www.page', 'Page', required=True, ondelete='CASCADE',
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    language = fields.Many2One('ir.lang', 'Idioma', required=True,
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    uri = fields.Char('URI',
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    main_uri = fields.Boolean('Main URI',
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)

    @fields.depends('page', 'language', 'uri',
        '_parent_page.name', '_parent_page.state')
    def on_change_language(self):
        if self.uri or not self.language:
            return
        name = None
        state = 'draft'
        if self.page and self.page.name:
            name = self.page.name
            state = self.page.state or 'draft'
        if not name:
            return
        self.uri = Page._uri_from_name(name, self.language.code, state=state)

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

    name = fields.Char('Name', required=True,
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    model = fields.Many2One('ir.model', 'Model', required=True,
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    page = fields.Many2One('www.page', 'Page', ondelete='CASCADE',
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    page_state = fields.Function(fields.Char('Page State'),
        'on_change_with_page_state')
    schema = fields.One2Many('www.schema', 'component', "Schema",
        size=1, add_remove=[('component', '=', None)],
        states=_CHILD_PAGE_STATES, depends=_CHILD_PAGE_DEPENDS)
    preview = fields.Function(
        fields.Binary('HTML Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

    @fields.depends('page', '_parent_page.state')
    def on_change_with_page_state(self, name=None):
        if self.page:
            return self.page.state
        return None

    @classmethod
    def delete(cls, elements):
        pool = Pool()
        Schema = pool.get('www.schema')
        schemas = [
            schema for element in elements
            for schema in (getattr(element, 'schema', None) or [])
        ]
        if schemas:
            Schema.delete(schemas)
        super().delete(elements)

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
    def _preview_char_value(cls, field):
        return 'Preview'

    @classmethod
    def _preview_text_value(cls, field):
        return (
            'Preview content. This placeholder is shown until a schema with '
            'real content is assigned.'
        )

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
        if isinstance(field, fields.Binary):
            return cls._preview_image().encode('utf-8')
        if isinstance(field, fields.Char):
            return cls._preview_char_value(field)
        if isinstance(field, fields.Text):
            return cls._preview_text_value(field)
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
            return cls._build_preview_schema_with_values(schema)
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

    component = fields.Many2One('www.element', 'Element',
        ondelete='CASCADE')
    model_name = fields.Function(fields.Char('Model Name'),
        'on_change_with_model_name')
    page_state = fields.Function(fields.Char('Page State'),
        'on_change_with_page_state')
    visible_fields = fields.Function(
        fields.MultiSelection('get_schema_fields', 'Visible Fields'),
        'on_change_with_visible_fields')
    # schema extensions hide the rest inline with:
    # states={'invisible': ~Eval('visible_fields', []).contains('field_name')}
    # depends=['visible_fields']

    @classmethod
    def _schema_content_fields(cls):
        return [
            name for name, field in cls._fields.items()
            if 'visible_fields' in (getattr(field, 'depends', []) or [])
            ]

    @classmethod
    def get_schema_fields(cls):
        return [(name, cls._fields[name].string or name)
            for name in cls._schema_content_fields()]

    @classmethod
    def _schema_fields_for_model(cls, model_name):
        content_fields = cls._schema_content_fields()
        if not model_name:
            return content_fields

        try:
            component = Pool().get(model_name)
        except Exception:
            return content_fields
        fields_ = getattr(component, '__fields__', None)
        if callable(fields_):
            fields_ = fields_()
        if isinstance(fields_, str):
            fields_ = [fields_]
        fields_ = [name for name in (fields_ or []) if name in content_fields]
        if 'background' in content_fields and 'background' not in fields_:
            fields_.append('background')
        if fields_:
            return fields_
        return content_fields

    @fields.depends('component', '_parent_component.model')
    def on_change_with_model_name(self, name=None):
        if self.component and self.component.model:
            return self.component.model.name
        return None

    @fields.depends('component', '_parent_component.page_state')
    def on_change_with_page_state(self, name=None):
        if self.component:
            return self.component.page_state
        return None

    @fields.depends('component', '_parent_component.model')
    def on_change_with_visible_fields(self, name=None):
        model_name = None
        if self.component and self.component.model:
            model_name = self.component.model.name
        return self._schema_fields_for_model(model_name)


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

    site = fields.Many2One('www.site', 'Site', required=True,
        ondelete='CASCADE')

    @classmethod
    def _get_resources(cls):
        return super()._get_resources() + ['www.page']


class VoyagerMenu(metaclass=PoolMeta):
    __name__ = 'www.menu'

    site = fields.Many2One('www.site', 'Site', required=True,
        ondelete='CASCADE')
    uri = fields.Many2One(
        'www.uri', 'URI',
        domain=[('main_uri', '=', None)],
        states={
            'invisible': Eval('type') != 'internal',
            'required': Eval('type') == 'internal',
        },
        depends=['type'],
        ondelete='SET NULL',
    )

    component = fields.Many2One('www.element', 'Element',
        states={
            'invisible': Eval('type') != 'component',
        },
        depends=['type'],
        ondelete='SET NULL')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.type.selection.append(('component', 'Element'))


class SiteLang(ModelSQL):
    __name__ = 'www.site.lang'

    site = fields.Many2One('www.site', 'Site', required=True,
        ondelete='CASCADE')
    language = fields.Many2One('ir.lang', 'Language', required=True)


class VoyagerSite(metaclass=PoolMeta):
    __name__ = 'www.site'

    header = fields.Many2One('www.element', 'Header', ondelete='SET NULL')
    footer = fields.Many2One('www.element', 'Footer', ondelete='SET NULL')
    layout = fields.Many2One('www.element', 'Layout', ondelete='SET NULL')
    langs = fields.Many2Many('www.site.lang', 'site', 'language', 'Languages')

    @classmethod
    def delete(cls, sites):
        pool = Pool()
        Page = pool.get('www.page')
        URI = pool.get('www.uri')
        site_ids = [site.id for site in sites if getattr(site, 'id', None)]

        if site_ids:
            pages = Page.search([
                    ('site', 'in', site_ids),
                    ])
            if pages:
                Page.delete(pages)

            uris = URI.search([
                    ('site', 'in', site_ids),
                    ])
            if uris:
                URI.delete(uris)

        super().delete(sites)


class ComponentCMS(Component):
    __fields__ = []
