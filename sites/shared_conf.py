import os
from os.path import join, dirname
from datetime import datetime

import alabaster


# Alabaster theme + mini-extension
html_theme_path = [alabaster.get_path()]
extensions = ['alabaster', 'sphinx.ext.intersphinx']

# Paths relative to invoking conf.py - not this shared file
html_static_path = [join('..', '_shared_static')]
html_theme = 'alabaster'
html_theme_options = {
    'logo': 'logo.png',
    'logo_name': True,
    'logo_text_align': 'center',
    'description': "Pythonic remote execution",
    'github_user': 'fabric',
    'github_repo': 'fabric',
    'travis_button': True,
    # TODO: enable once one of the two happens:
    # - 2.0.0 becomes master branch in repo
    # - alabaster grows that arbitrary button functionality so we can aim it at
    # v2 specifically
    #'codecov_button': True,
    'gittip_user': 'bitprophet',
    'analytics_id': 'UA-18486793-1',

    'link': '#3782BE',
    'link_hover': '#3782BE',

    # Wide enough that 80-col code snippets aren't truncated on default font
    # settings (at least for bitprophet's Chrome-on-OSX-Yosemite setup)
    'page_width': '1024px',
}
html_sidebars = {
    '**': [
        'about.html',
        'navigation.html',
        'searchbox.html',
        'donate.html',
    ]
}

on_rtd = os.environ.get('READTHEDOCS') == 'True'
on_travis = os.environ.get('TRAVIS', False)
on_dev = not (on_rtd or on_travis)

# Everything intersphinx's to Python, and to (local-or-remote) Invoke (and its
# www)
inv_target = join(
    dirname(__file__),
    '..', '..', 'invoke', 'sites', 'docs', '_build'
)
if not on_dev:
    inv_target = 'http://docs.pyinvoke.org/en/latest/'
inv_www_target = join(
    dirname(__file__),
    '..', '..', 'invoke', 'sites', 'www', '_build'
)
if not on_dev:
    inv_www_target = 'http://pyinvoke.org/'
# ... and Paramiko (docs)
para_target = join(
    dirname(__file__),
    '..', '..', 'paramiko', 'sites', 'docs', '_build'
)
if not on_dev:
    para_target = 'http://docs.paramiko.org/en/latest/'
intersphinx_mapping = {
    'python': ('http://docs.python.org/2.6', None),
    'invoke': (inv_target, None),
    'invoke_www': (inv_www_target, None),
    'paramiko': (para_target, None),
}

# Regular settings
project = 'Fabric'
year = datetime.now().year
copyright = '%d Jeff Forcier' % year
master_doc = 'index'
templates_path = ['_templates']
exclude_trees = ['_build']
source_suffix = '.rst'
default_role = 'obj'
