from datetime import date
from xml.sax.saxutils import escape

from dominate.tags import div
from dominate.util import raw
from werkzeug.exceptions import HTTPException
from trytond.config import config
from trytond.exceptions import UserError
from trytond.i18n import gettext as _

from trytond.model import ModelSQL, ModelView, Workflow, fields, sequence_ordered
from trytond.pool import Pool, PoolMeta
from trytond.i18n import gettext
from trytond.modules.voyager.voyager import Component, Endpoint, VoyagerContext
from trytond.pyson import Bool, Eval
from trytond.transaction import Transaction
from trytond.url import http_host

PAGE_STATES = {'readonly': Eval('state') != 'draft'}
PAGE_DEPENDS = ['state']
CHILD_PAGE_STATES = {
    'readonly': Bool(Eval('page')) & (Eval('_parent_page', {}).get('state') != 'draft')
}
CHILD_PAGE_DEPENDS = ['page', '_parent_page.state']


class _LayoutRenderProxy:
    __slots__ = ('_layout', 'content', 'title')

    def __init__(self, layout, content, title):
        self._layout = layout
        self.content = content
        self.title = title

    def __getattr__(self, name):
        return getattr(self._layout, name)


def _render_layout_instance(layout, content, title):
    if layout is None:
        return content.render() if hasattr(content, 'render') else str(content or '')

    try:
        return layout.render(content=content, title=title)
    except TypeError as exc:
        if "unexpected keyword argument 'content'" not in str(exc):
            raise
    try:
        return layout.render(content, title=title)
    except TypeError as exc:
        if "unexpected keyword argument 'title'" not in str(exc):
            raise

    proxy = _LayoutRenderProxy(layout, content, title)
    if hasattr(proxy, 'main'):
        try:
            proxy.main.children = []
        except Exception:
            pass
        try:
            proxy.main.add(content)
        except Exception:
            pass
    return type(layout).render(proxy)


class _PreviewAdapter:

    @staticmethod
    def build(endpoint, values=None):
        return '#'


class _PreviewEndpointArgs(dict):

    def __missing__(self, key):
        return []


class _PreviewCache(dict):

    def set(self, key, value):
        self[key] = value


class _PreviewPlaceholder:

    def __init__(self, **values):
        self.__dict__.update(values)

    def __getattr__(self, name):
        value = self.__dict__.get(name)
        if value is None:
            value = self.__class__()
            self.__dict__[name] = value
        return value

    def __getitem__(self, key):
        return getattr(self, key)

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __call__(self, *args, **kwargs):
        return self.__class__()

    def __str__(self):
        return ''

    def __repr__(self):
        return 'PreviewPlaceholder()'


def _build_preview_voyager_context(site):
    return VoyagerContext(
        site=site,
        session=_PreviewPlaceholder(preview=True),
        cache=_PreviewCache(),
        request=_PreviewPlaceholder(preview=True),
        adapter=_PreviewAdapter(),
        endpoint_args=_PreviewEndpointArgs(),
        web_prefix='',
    )


def _get_voyager_context(site=None):
    context = getattr(Transaction(), 'context', {}) or {}
    current = context.get('voyager_context')
    if not current:
        return VoyagerContext(site=site)
    values = {
        key: value for key, value in vars(current).items()
        if key != 'site'
    }
    values['site'] = site or getattr(current, 'site', None)
    return VoyagerContext(**values)

class Page(Workflow, ModelSQL, ModelView):
    __name__ = 'www.page'

    name = fields.Char('Name', required=True, translate=True,
        states=PAGE_STATES, depends=PAGE_DEPENDS)
    site = fields.Many2One('www.site', 'Site', required=True,
        ondelete='CASCADE',
        states=PAGE_STATES, depends=PAGE_DEPENDS)
    available_languages = fields.Function(
        fields.Many2Many('ir.lang', None, None, 'Available Languages'),
        'on_change_with_available_languages')
    main_uri_language = fields.Many2One(
        'ir.lang', 'Idioma',
        domain=[('id', 'in', Eval('available_languages'))],
        states=PAGE_STATES, depends=PAGE_DEPENDS + ['site', 'available_languages'])
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
    uris = fields.Function(
        fields.One2Many('www.uri', None, 'URIs',
            states=PAGE_STATES, depends=PAGE_DEPENDS),
        'get_uris', setter='set_uris')
    element = fields.One2Many(
        'www.element', 'page', 'Elements',
        order=[('sequence', 'ASC')],
        states=PAGE_STATES, depends=PAGE_DEPENDS,
    )
    preview = fields.Function(
        fields.Binary('Page Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

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

    @staticmethod
    def default_state():
        return 'draft'

    @fields.depends('site')
    def on_change_with_available_languages(self, name=None):
        pool = Pool()
        Lang = pool.get('ir.lang')
        codes = self._site_lang_codes(self.site)
        return [l.id for l in Lang.search([('code', 'in', codes)])]

    @fields.depends('site', 'main_uri_language', 'available_languages')
    def on_change_site(self):
        if self.main_uri_language and self.main_uri_language.id in (
                self.available_languages or []):
            return
        if self.site and self.site.main_lang:
            self.main_uri_language = self.site.main_lang
        else:
            self.main_uri_language = None

    @classmethod
    def get_uris(cls, pages, name):
        pool = Pool()
        URI = pool.get('www.uri')
        result = {}
        for page in pages:
            page_id = getattr(page, 'id', None)
            if not page_id:
                result[page_id] = []
                continue
            resource_ref = f'{cls.__name__},{page_id}'
            result[page_id] = [
                uri.id for uri in URI.search([
                        ('resource', '=', resource_ref),
                        ], order=[('id', 'ASC')])
            ]
        return result

    @classmethod
    def set_uris(cls, pages, name, value):
        """
        Compatibility setter.

        `uris` is computed from `www.uri` (resource = "www.page,<id>").
        Some clients (including the Tryton client when editing embedded lines)
        still send One2Many commands for this field when saving a page. In that
        case we must forward the changes to `www.uri` instead of ignoring them,
        otherwise the UI appears to "revert" the changes after save.
        """
        if not value:
            return

        pool = Pool()
        URI = pool.get('www.uri')

        for page in pages:
            page_id = getattr(page, 'id', None)
            if not page_id:
                continue
            resource_ref = f'{cls.__name__},{page_id}'

            for command in value:
                if not command:
                    continue
                action = command[0]

                # Tryton One2Many commands may be expressed either as:
                # ('write', [ids], vals) / ('create', [vals]) / ('delete', [ids])
                # or the numeric form: (1, id, vals) / (0, 0, vals) / (2, id)
                if action == 'write' or action == 1:
                    if action == 1:
                        ids, vals = [command[1]], command[2]
                    else:
                        ids, vals = command[1], command[2]
                    if not ids or not vals:
                        continue
                    uris = URI.search([
                            ('id', 'in', ids),
                            ('resource', '=', resource_ref),
                            ])
                    if uris:
                        URI.write(uris, vals)
                elif action == 'delete' or action == 2:
                    ids = [command[1]] if action == 2 else command[1]
                    if not ids:
                        continue
                    uris = URI.search([
                            ('id', 'in', ids),
                            ('resource', '=', resource_ref),
                            ])
                    if uris:
                        URI.delete(uris)
                elif action == 'create' or action == 0:
                    records = [command[2]] if action == 0 else (command[1] or [])
                    if not records:
                        continue
                    to_create = []
                    for vals in records:
                        vals = dict(vals or {})
                        vals.setdefault('resource', resource_ref)
                        if 'site' not in vals:
                            site = getattr(page, 'site', None)
                            if getattr(site, 'id', None):
                                vals['site'] = site.id
                        to_create.append(vals)
                    if to_create:
                        URI.create(to_create)
                else:
                    # Ignore other One2Many operations; `uris` is computed.
                    continue

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
        URI = pool.get('www.uri')
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
        resource_ref = f'{cls.__name__},{page.id}'
        uris = URI.search([
                ('site', '=', page.site.id),
                ('resource', '!=', resource_ref),
                ('uri', 'in', target_uris),
                ])
        page_ids = set()
        for uri in uris:
            resource = getattr(uri, 'resource', None)
            if not resource:
                continue
            # The Reference is stored as "model,id".
            if isinstance(resource, str):
                if not resource.startswith(f'{cls.__name__},'):
                    continue
                try:
                    page_ids.add(int(resource.split(',', 1)[1]))
                except Exception:
                    continue
            else:
                try:
                    model_name, rec_id = resource
                except Exception:
                    continue
                if model_name == cls.__name__ and rec_id:
                    page_ids.add(rec_id)
        if not page_ids:
            return []
        return cls.search([
                ('id', 'in', list(page_ids)),
                ('state', '=', 'published'),
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
    def _site_lang_codes(cls, site):
        pool = Pool()
        langs = []
        if getattr(site, 'langs', None):
            langs = [
                lang.code for lang in site.langs
                if getattr(lang, 'code', None)
            ]
        elif hasattr(site, 'id') and site.id:
            try:
                SiteLang = pool.get('www.site.lang')
                site_langs = SiteLang.search([('site', '=', site.id)])
                langs = [
                    sl.language.code for sl in site_langs
                    if getattr(sl, 'language', None)
                    and getattr(sl.language, 'code', None)
                ]
            except Exception:
                pass
        return langs

    @classmethod
    def _default_uris(cls, name, site=None, state='published'):
        langs = cls._site_lang_codes(site)
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
    def delete(cls, pages):
        cls._delete_generated_uris(pages)
        super().delete(pages)

    @classmethod
    def write(cls, pages, values, *args):
        values = values.copy()
        super().write(pages, values, *args)
        if 'state' in values:
            cls.generate_uri(pages)
        elif 'name' in values or 'site' in values:
            cls.generate_uri(pages)

    @classmethod
    def validate(cls, pages):
        super().validate(pages)
        for page in pages:
            page.check_main_uri()

    def check_main_uri(self):
        pool = Pool()
        URI = pool.get('www.uri')
        if not getattr(self, 'id', None) or not getattr(self, 'site', None):
            return
        resource_ref = f'{self.__name__},{self.id}'
        main_uris = URI.search([
                ('site', '=', self.site.id),
                ('resource', '=', resource_ref),
                ('main_uri', '=', None),
                ])
        if self.main_uri_language and len(main_uris) > 1:
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
            selected_code = (
                page.main_uri_language.code
                if getattr(page, 'main_uri_language', None)
                and getattr(page.main_uri_language, 'code', None)
                else None
            )

            # Build desired rows, but preserve any manually edited URI value
            # for a given language when the record already exists. The purpose
            # of this button is to (re)sync records, not to override manual
            # slugs.
            desired_rows = cls._default_uris(
                page.name, page.site, state=page.state or 'draft')
            desired_uris = []
            Lang = pool.get('ir.lang')
            for row in desired_rows:
                uri_value = row.get('uri')
                language_id = row.get('language')
                if not language_id:
                    continue
                language = Lang(language_id)
                code = language.code if language else None
                existing = existing_uris_by_language.get(code) if code else None
                if existing and getattr(existing, 'uri', None):
                    uri_value = existing.uri
                # Ensure any existing URI missing endpoint gets fixed by the
                # regeneration.
                if uri_value:
                    desired_uris.append((uri_value, language_id))
            if not desired_uris:
                continue

            new_uris = []
            selected_uri = None

            for uri_value, language_id in desired_uris:
                language = Lang(language_id)
                code = language.code if language else None
                site_langs = cls._site_lang_codes(page.site)
                if code not in site_langs:
                    continue

                key = (uri_value, code)

                if key in existing_uris:
                    uri = existing_uris.pop(key)
                    existing_uris_by_language.pop(code, None)
                elif code in existing_uris_by_language:
                    uri = existing_uris_by_language.pop(code)
                    existing_uris.pop(
                        (uri.uri, uri.language.code if uri.language else None),
                        None)
                else:
                    uri = None

                if uri is None:
                    uri = URI.create([{
                                'resource': resource_ref,
                                'site': page.site.id,
                                'uri': uri_value,
                                'language': language.id,
                                'endpoint': endpoint.id,
                                }])[0]
                else:
                    URI.write([uri], {
                            'site': page.site.id,
                            'uri': uri_value,
                            'language': language.id,
                            'endpoint': endpoint.id,
                            })

                new_uris.append(uri)
                if selected_code and code == selected_code:
                    selected_uri = uri

            main_uri = selected_uri or (new_uris[0] if new_uris else None)
            if not main_uri:
                continue

            if (not getattr(page, 'main_uri_language', None)
                    or page.main_uri_language.id != main_uri.language.id):
                cls.write([page], {'main_uri_language': main_uri.language.id})

            # Ensure the chosen main URI satisfies the domain
            # (main_uri must be NULL). Clear first to avoid transient states
            # where another record points to the would-be main.
            URI.write(new_uris, {'main_uri': None})
            others = [uri for uri in new_uris if uri is not main_uri]
            if others:
                URI.write(others, {'main_uri': main_uri.id})

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

            # Records already created/written above.
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

    @classmethod
    def render_preview_content(cls, page):
        pool = Pool()
        Wrapper = pool.get('www.content.wrapper')
        site = page.site if page and page.site else None
        with Transaction().set_context(
                voyager_context=_build_preview_voyager_context(site),
                voyager_cms_preview=True):
            rendered = Wrapper(page=page).render()
        if hasattr(rendered, 'render'):
            content = rendered.render()
        else:
            content = str(rendered or '')
        if not (content or '').strip():
            return Element._build_preview_document(
                '<div style="padding: 1rem; color: #666; font-family: '
                'sans-serif;">'
                'No preview available.'
                '</div>',
                site=site)
        return Element._build_preview_document(content, site=site)

    @fields.depends('site', 'name', 'element')
    def get_preview(self, name=None):
        if not self.site:
            return Element._build_preview_document(
                '<div style="padding: 1rem; color: #666; font-family: '
                'sans-serif;">'
                'Select a site to preview the page.'
                '</div>',
                site=None).encode()
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

    name = fields.Char('Name', required=True,
        states=CHILD_PAGE_STATES, depends=CHILD_PAGE_DEPENDS)
    valid_models = fields.Function(
        fields.Many2Many('ir.model', None, None, 'Valid Models'),
        'get_valid_models')
    model = fields.Many2One('ir.model', 'Model', required=True,
        domain=[
            ('id', 'in', Eval('valid_models', [])),
        ],
        states=CHILD_PAGE_STATES, depends=CHILD_PAGE_DEPENDS + ['valid_models'])
    page = fields.Many2One('www.page', 'Page', ondelete='CASCADE',
        states=CHILD_PAGE_STATES, depends=CHILD_PAGE_DEPENDS)
    page_state = fields.Function(fields.Char('Page State'),
        'on_change_with_page_state')
    schema = fields.One2Many('www.schema', 'element', "Schema",
        size=1, add_remove=[('element', '=', None)],
        states=CHILD_PAGE_STATES, depends=CHILD_PAGE_DEPENDS)
    show_preview_fields = fields.Boolean(
        'Show Preview Fields',
        states=CHILD_PAGE_STATES, depends=CHILD_PAGE_DEPENDS)
    preview = fields.Function(
        fields.Binary('HTML Preview', filename='preview_filename'),
        'get_preview')
    preview_filename = fields.Function(
        fields.Char('Preview Filename', readonly=True),
        'get_preview_filename')

    @staticmethod
    def default_show_preview_fields():
        return True

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('/form/notebook/page[@id="preview"]', 'states', {
                    'invisible': ~Bool(Eval('show_preview_fields', True)),
                    }),
            ]

    @classmethod
    def default_valid_models(cls):
        return cls._element_model_ids()

    @staticmethod
    def _element_model_ids():
        pool = Pool()
        Model = pool.get('ir.model')
        model_names = sorted({
            name for name, klass in pool.iterobject()
            if issubclass(klass, ComponentCMS)
        })
        if not model_names:
            return []
        return [model.id for model in Model.search([
                    ('name', 'in', model_names),
                    ])]

    @classmethod
    def get_valid_models(cls, elements, name):
        model_ids = cls._element_model_ids()
        return {
            element.id: model_ids
            for element in elements
        }

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
            return None
        if isinstance(field, fields.One2Many):
            return []
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
            return 'Preview'
        if isinstance(field, fields.Text):
            return (
                'Preview content. This placeholder is shown until a schema '
                'with real content is assigned.'
            )
        return None

    @classmethod
    def _build_preview_schema(cls, include_visual=True):
        pool = Pool()
        Schema = pool.get('www.schema')

        schema = Schema()
        for field_name, field in Schema._fields.items():
            if field_name in {
                    'element', 'id', 'create_uid', 'create_date',
                    'write_uid', 'write_date', 'rec_name', 'model_name'}:
                continue
            value = cls._preview_value_for_field(field_name, field)
            if value is not None or isinstance(field, fields.Many2One):
                setattr(schema, field_name, value)
        return schema

    @classmethod
    def _build_preview_schema_with_values(cls, schema):
        preview_schema = cls._build_preview_schema(include_visual=bool(schema))
        if not schema:
            return preview_schema

        for field_name in getattr(preview_schema, '_fields', {}):
            field = preview_schema._fields[field_name]
            if isinstance(field, fields.One2Many):
                continue
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
    def _preview_enabled(cls, element):
        context = getattr(Transaction(), 'context', {}) or {}
        if not context.get('voyager_cms_preview'):
            return True
        return bool(getattr(element, 'show_preview_fields', True))

    @classmethod
    def get_element_schema(cls, model, schema=None, show_preview_fields=True):
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
    def get_element_kwargs(cls, model, schema=None, show_preview_fields=True):
        schema = cls.get_element_schema(
            model, schema, show_preview_fields=show_preview_fields)
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
    def render_with_site_layout(
            cls, site, content, title, preview_chrome=False):
        if not site:
            return content

        context = getattr(Transaction(), 'context', {}) or {}
        preview = bool(context.get('voyager_cms_preview'))
        pool = Pool()
        layout_element = site.layout
        layout = None
        if layout_element and layout_element.model:
            LayoutModel = pool.get(layout_element.model.name)
            layout = LayoutModel(
                **cls.get_element_kwargs(
                    LayoutModel, layout_element.schema))
        with Transaction().set_context(
                voyager_context=(
                    _build_preview_voyager_context(site)
                    if preview else _get_voyager_context(site)),
                voyager_cms_preview=preview):
            if preview and site and not preview_chrome:
                with div() as wrapped:
                    header_element = site.header
                    if header_element and header_element.model:
                        try:
                            HeaderModel = pool.get(header_element.model.name)
                            HeaderModel(**Element.get_element_kwargs(
                                HeaderModel, header_element.schema)).tag()
                        except Exception:
                            pass

                    if content:
                        if hasattr(content, 'render'):
                            wrapped.add(content)
                        else:
                            raw(str(content))

                    footer_element = site.footer
                    if footer_element and footer_element.model:
                        try:
                            FooterModel = pool.get(footer_element.model.name)
                            FooterModel(**Element.get_element_kwargs(
                                FooterModel, footer_element.schema)).tag()
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
            rendered = _render_layout_instance(layout, content, title)
        return rendered

    @classmethod
    def render_element_content(
            cls, model_name, schema=None, show_preview_fields=True):
        pool = Pool()
        ElementModel = pool.get(model_name)
        with div() as content:
            tag = ElementModel(
                **cls.get_element_kwargs(
                    ElementModel, schema)
            ).tag()
            if tag is not None and not getattr(content, 'children', None):
                content.add(tag)
        return content

    @classmethod
    def render_preview_content(cls, element):
        site = element.page.site if element.page and element.page.site else None
        with Transaction().set_context(
                voyager_context=_build_preview_voyager_context(site),
                voyager_cms_preview=True):
            rendered = cls.render_element_content(
                element.model.name,
                element.schema)
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
        return cls._build_preview_document(
            content, site=site, model_name=element.model.name)

    @fields.depends('model', 'schema', 'show_preview_fields', 'page')
    def get_preview(self, name=None):
        if not self.page:
            return b''
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

    element = fields.Many2One('www.element', 'Element',
        ondelete='CASCADE')
    model_name = fields.Function(fields.Char('Model Name'),
        'on_change_with_model_name')
    page_state = fields.Function(fields.Char('Page State'),
        'on_change_with_page_state')
    visible_fields = fields.Function(
        fields.MultiSelection('get_schema_fields', 'Visible Fields'),
        'on_change_with_visible_fields')

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
            model = Pool().get(model_name)
        except Exception:
            return content_fields
        fields_ = getattr(model, '__fields__', None)
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

    @fields.depends('element', '_parent_element.model')
    def on_change_with_model_name(self, name=None):
        if self.element and self.element.model:
            return self.element.model.name
        return None

    @fields.depends('element', '_parent_element.page_state')
    def on_change_with_page_state(self, name=None):
        if self.element:
            return self.element.page_state
        return None

    @fields.depends('element', '_parent_element.model')
    def on_change_with_visible_fields(self, name=None):
        model_name = None
        if self.element and self.element.model:
            model_name = self.element.model.name
        return self._schema_fields_for_model(model_name)


class ContentWrapper(Endpoint):
    __name__ = 'www.content.wrapper'
    _url = '/content-wrapper'
    #TODO: what we do with the type??
    _type = []
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
        layout_element = self.site.layout if self.site else None
        layout = None
        if layout_element and layout_element.model:
            LayoutModel = pool.get(layout_element.model.name)
            layout = LayoutModel(
                **Element.get_element_kwargs(
                    LayoutModel, layout_element.schema))

        def _render_layout(content, title):
            if self.site:
                with div() as wrapped:
                    header_element = self.site.header
                    if header_element and header_element.model:
                        try:
                            HeaderModel = pool.get(header_element.model.name)
                            HeaderModel(**Element.get_element_kwargs(
                                HeaderModel, header_element.schema)).tag()
                        except Exception:
                            pass

                    if content:
                        if hasattr(content, 'render'):
                            wrapped.add(content)
                        else:
                            raw(str(content))

                    footer_element = self.site.footer
                    if footer_element and footer_element.model:
                        try:
                            FooterModel = pool.get(footer_element.model.name)
                            FooterModel(**Element.get_element_kwargs(
                                FooterModel, footer_element.schema)).tag()
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
            return _render_layout_instance(layout, content, title)

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

    element = fields.Many2One('www.element', 'Element',
        states={
            'invisible': Eval('type') != 'element',
        },
        depends=['type'],
        ondelete='SET NULL')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        if ('element', 'Element') not in cls.type.selection:
            cls.type.selection.append(('element', 'Element'))


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
    main_lang = fields.Many2One('ir.lang', 'Main Language', required=True)
    langs = fields.Many2Many('www.site.lang', 'site', 'language', 'Languages')

    @staticmethod
    def _allow_page_state_in_environment(page):
        # en produccio nomes deixa veure published
        if not page:
            return True
        production = config.getboolean('database', 'production', default=False)
        if production:
            return page.state == 'published'
        # en dev nomes deixa veure draft
        return page.state == 'draft'

    def match_request(self, request, web_prefix=None):
        pool = Pool()
        VoyagerURI = pool.get('www.uri')

        web_map, adapter, endpoint_args, error_handlers = self.get_site_info(
            web_prefix)

        try:
            language = None
            request_path = request.path
            if web_prefix:
                request_path = request.path.replace(
                    web_prefix, '', 1)

            if self.route_method == 'uri':
                voyager_uri = VoyagerURI.search([
                    ('site', '=', self.id),
                    ('uri', '=', request_path)], limit=1)

                if voyager_uri:
                    voyager_uri = voyager_uri[0]
                    resource = voyager_uri.resource
                    resource_model = getattr(resource, '__name__', None)
                    if resource_model == 'www.page':
                        # si l'estat no toca, ignora la uri
                        if not self._allow_page_state_in_environment(resource):
                            voyager_uri = None

                if voyager_uri:
                    endpoint = voyager_uri.endpoint.name
                    resource = voyager_uri.resource
                    resource_model = getattr(resource, '__name__', None)
                    args = {}

                    if not resource_model:
                        resource_model = str(resource).split(',')[0]
                    try:
                        EndpointModel = pool.get(endpoint)
                    except Exception:
                        EndpointModel = None
                    if EndpointModel:
                        for field_name, field in EndpointModel._fields.items():
                            if (isinstance(field, fields.Many2One)
                                    and field.model_name == resource_model):
                                args[field_name] = resource.id
                    if voyager_uri.language:
                        language = voyager_uri.language.code
                else:
                    if request.method:
                        endpoint, args = adapter.match(request.path,
                            request.method)
                    else:
                        endpoint, args = adapter.match(request.path)
            elif self.route_method == 'endpoint':
                if request.method:
                    endpoint, args = adapter.match(request.path,
                        request.method)
                else:
                    endpoint, args = adapter.match(request.path)
        except HTTPException as e:
            if e.code in error_handlers:
                endpoint = error_handlers[e.code]
                return (None, None, None, None, None,
                    adapter.build(endpoint.__name__, None))
            raise e
        return endpoint, args, adapter, endpoint_args, language, None

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

    @staticmethod
    def _render_child_element(name=None, schema=None, element=None):
        pool = Pool()
        if element is not None:
            name = element.model.name if element and element.model else None
            schema = element.schema if element else schema
        if not name:
            return None

        try:
            ElementModel = pool.get(name)
            if schema:
                return ElementModel(schema=schema[0]).tag()
            return ElementModel().tag()
        except Exception:
            return None
