# This file is part voyager_cms module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import utils

def register():
    Pool.register(
        utils.Page,
        utils.Component,
        utils.Schema,
        utils.PageDummy,
        utils.VoyagerURI,
        utils.VoyagerMenu,
        utils.VoyagerSite,
        module='voyager_cms', type_='model')
    Pool.register(
        module='voyager_cms', type_='wizard')
    Pool.register(
        module='voyager_cms', type_='report')
