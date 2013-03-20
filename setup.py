#!/usr/bin/env python
from distutils.core import setup

setup(
    name = 'PNDstore',
    version = '1.0.4',
    description = 'Install and update PNDs',
    long_description = open('README.rst').read(),
    author = 'Randy Heydon',
    author_email = 'randy.heydon@clockworklab.net',
    url = 'https://github.com/Tempel/PNDstore',
    packages = ['pndstore_core', 'pndstore_gui'],
    package_data = {'pndstore_core': ['cfg/*'], 'pndstore_gui': ['PNDstore.glade']},
    scripts = ['PNDstore', 'pndst'],
    license = 'LGPL',
)
