# This file is part voyager_cms module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import cms

def register():
    Pool.register(
        cms.File,
        cms.Page,
        cms.Element,
        cms.Schema,
        cms.ContentWrapper,
        cms.FileWrapper,
        cms.VoyagerURI,
        cms.VoyagerMenu,
        cms.SiteLang,
        cms.VoyagerSite,
        module='voyager_cms', type_='model')
