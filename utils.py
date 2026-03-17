from trytond.model import ModelSQL, ModelView, fields, sequence_ordered
from trytond.pool import Pool, PoolMeta
from trytond.exceptions import UserError
from trytond.i18n import gettext as _
from trytond.modules.voyager.voyager import Endpoint
from trytond.tools import slugify
from dominate.tags import div
from trytond.pyson import Eval
from trytond.transaction import Transaction
      
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

    component = fields.One2Many(
        'www.component', 'page', 'Components',
        order=[('sequence', 'ASC')],
    )

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

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

    # ---------------------------------------------------------------------
    # CRUD
    # ---------------------------------------------------------------------

    @classmethod
    def create(cls, vlist):
        vlist = [values.copy() for values in vlist]
        for values in vlist:
            if values.get('name'):
                uri_es, uri_en, uri_ca = cls._uris_from_name(values['name'])
                values.setdefault('uri_es', uri_es)
                values.setdefault('uri_en', uri_en)
                values.setdefault('uri_ca', uri_ca)
        return super().create(vlist)

    @classmethod
    def write(cls, pages, values, *args):
        values = values.copy()
        if values.get('name'):
            uri_es, uri_en, uri_ca = cls._uris_from_name(values['name'])
            values.setdefault('uri_es', uri_es)
            values.setdefault('uri_en', uri_en)
            values.setdefault('uri_ca', uri_ca)
        return super().write(pages, values, *args)

    # ---------------------------------------------------------------------
    # Setup / Validation
    # ---------------------------------------------------------------------

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

    # ---------------------------------------------------------------------
    # Generate URI
    # ---------------------------------------------------------------------

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

            # ---- main_uri logic ----
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


class Component(sequence_ordered(), ModelSQL, ModelView):
    __name__ = 'www.component'

    name = fields.Char('Name', required=True)
    model = fields.Many2One('ir.model', 'Model', required=True)
    page = fields.Many2One('www.page', 'Page')
    schema = fields.Many2One('www.schema', 'Schema')


class Schema(ModelSQL, ModelView):
    __name__ = 'www.schema'

    component = fields.Many2One('www.component', 'Component')
    icon = fields.Char('Icon')
    menu = fields.Many2One(
        'www.menu', 'Menu',
        domain=[('parent', '=', None)],
    )
    model_name = fields.Function(fields.Char('Model Name'),
        'on_change_with_model_name')


class ContentWrapper(Endpoint):
    __name__ = 'www.content.wrapper'
    _url = '/content-wrapper'
    _type = 'www'
    page = fields.Many2One('www.page', 'Page')

    @classmethod
    def __register__(cls, module_name):
        cursor = Transaction().connection.cursor()
        cursor.execute(
            "SELECT id FROM ir_model WHERE name = %s",
            ('www.content.wrapper',))
        new = cursor.fetchone()
        cursor.execute(
            "SELECT id FROM ir_model WHERE name = %s",
            ('www.page.dummy',))
        old = cursor.fetchone()

        if old and not new:
            cursor.execute(
                "UPDATE ir_model SET name = %s WHERE id = %s",
                ('www.content.wrapper', old[0]))
        elif old and new and old[0] != new[0]:
            # Migrate existing URI endpoint references from old model id.
            cursor.execute(
                "UPDATE www_uri SET endpoint = %s WHERE endpoint = %s",
                (new[0], old[0]))

        super().__register__(module_name)

    def get_not_found_content(self):
        """Override this method to customize the 'page not found' content."""
        with div() as content:
            content.add(div(_("Page not found")))
        return content

    def get_not_found_title(self):
        """Override this method to customize the 'page not found' title."""
        return _("Page not found")

    def render(self):
        pool = Pool()
        layout_component = self.site.layout
        layout = None
        if layout_component and layout_component.model:
            LayoutModel = pool.get(layout_component.model.name)
            layout = LayoutModel()
            #layout = (
            #    LayoutModel(schema=layout_component.schema)
            #    if layout_component.schema
            #    else LayoutModel()
            #)
        def _render_layout(content, title):
            if layout is None:
                # Keep voyager_cms self-contained: render content without requiring
                # any layout model from external modules.
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
            for component in self.page.component:
                ComponentModel = pool.get(component.model.name)
                if component.schema:
                    ComponentModel(schema=component.schema).tag()
                else:
                    ComponentModel().tag()

        return _render_layout(content=page_content, title=self.page.name)

class VoyagerURI(metaclass=PoolMeta):
    __name__ = 'www.uri'

    @classmethod
    def _get_resources(cls):
        return super()._get_resources() + [
            'www.voyager.url',
            'www.page',
        ]
    

class VoyagerMenu(metaclass=PoolMeta):
    __name__ = 'www.menu'

    component = fields.Many2One('www.component', 'Component',
        states={
            'invisible': Eval('type') != 'component',
        },
        depends=['type'],)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls.type.selection.append(('component', 'Component'))


class VoyagerSite(metaclass=PoolMeta):
    __name__ = 'www.site'

    header = fields.Many2One('www.component', 'Header')
    footer = fields.Many2One('www.component', 'Footer')
    layout = fields.Many2One('www.component', 'Layout')
