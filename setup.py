#!/usr/bin/env python2
from distutils.core import setup

setup(
	name = 'PNDstore',
	version = '0',
	description = 'thing', #TODO: a real description
	author = 'Randy Heydon',
	author_email = 'randy.heydon@clockworklab.net',
	url = 'http://randy.heydon.selfip.net/Programs/PNDstore/', #TODO: check url
	packages = ['pndstore', 'pndstore_gui'],
    package_data = {'pndstore': ['cfg/*']}
	scripts = ['PNDstore', 'pndst'],
)
