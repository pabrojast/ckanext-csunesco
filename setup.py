# encoding: utf-8

from setuptools import setup, find_packages


setup(
    name='ckanext-csunesco',
    version='0.1.0',
    description='Citizen Science (UNESCO/IHP-WINS) extension for CKAN',
    long_description='',
    classifiers=[
        # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
    keywords='CKAN UNESCO citizen-science IHP-WINS',
    author='UNESCO IHP-WINS',
    author_email='',
    url='https://github.com/pabrojast/ckanext-csunesco',
    license='GNU Affero General Public License (AGPL) v3.0',
    packages=find_packages(exclude=['tests']),
    namespace_packages=['ckanext'],
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        # HTML sanitizer for user-supplied news/event bodies + rejection
        # reasons (see ckanext/csunesco/logic/sanitize.py). The code degrades
        # gracefully (strips all tags) if it is somehow absent.
        'bleach',
    ],
    entry_points="""
        [ckan.plugins]
        csunesco=ckanext.csunesco.plugin:CsunescoPlugin
        [babel.extractors]
        ckan = ckan.lib.extract:extract_ckan
    """,
    message_extractors={
        'ckanext': [
            ('**.py', 'python', None),
            ('**.js', 'javascript', None),
            ('**/csunesco/templates/**.html', 'ckan', None),
        ],
    },
)
