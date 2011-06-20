"""
This module populates the app database with installed and available
applications.  Consumers of this module will likely only need the functions
update_remote and update_local (and maybe update_local_file, if you're feeling
fancy).

On import, this module creates the database that stores package information and
creates the base set of tables that are expected to be in it.  This database is
created in the working directory specified by pndstore_core.options.
Therefore, you must ensure that options.working_dir is set to the desired
directory *before* this module is imported.

Concurrency note: Most functions here make changes to the database.  However,
they all create their own connections and cursors; since sqlite can handle
concurrent database writes automatically, these functions should be thread safe.
"""

import options, libpnd, urllib2, sqlite3, json, ctypes, warnings, time
import xml.etree.cElementTree as etree
from hashlib import md5

#This module currently supports these versions of the PND repository
#specification as seen at http://pandorawiki.org/PND_repository_specification
REPO_VERSION = (2.0, 3.0)

LOCAL_TABLE = 'local'
REPO_INDEX_TABLE = 'repo_index'
SEPCHAR = ';' # Character that defines list separations in the database.

# Minimum amount of time to wait between full updates (in seconds).
FULL_UPDATE_TIME = 3000000 # ~35 days.
# The substring that gets replaced in updates URLs, as given in the repo spec.
TIME_SUBSTRING = '%time%'

PXML_NAMESPACE = 'http://openpandora.org/namespaces/PXML'
xml_child = lambda s: '{%s}%s' % (PXML_NAMESPACE, s)

class RepoError(Exception): pass
class PNDError(Exception): pass


def sanitize_sql(name):
    """The execute method's parametrization does not work for table names.
    Therefore string formatting must be used, which bypasses the sqlite3
    package's sanitization.  This function takes a stab at sanitizing.  Since
    table names are put in double quotes (thereby representing an SQL
    identifier), any characters should be fine except double quotes.  But since
    all table names are URLs read from a JSON file, they likely won't include
    quotes, so this function is mostly useless."""
    return str(name).translate(None, '"')
    #TODO: Remove str call once/if it's not needed.


def create_table(cursor, name):
    name = sanitize_sql(name)
    cursor.execute("""Create Table If Not Exists "%s" (
        id Text Primary Key,
        uri Text,
        version Text,
        title Text,
        description Text,
        info Text,
        size Int,
        md5 Text,
        modified_time Int,
        rating Int,
        author_name Text,
        author_website Text,
        author_email Text,
        vendor Text,
        icon Text,
        previewpics Text,
        licenses Text,
        source Text,
        categories Text,
        applications Text,
        appdatas Text
        )""" % name)


def update_remote_package(table, pkg, cursor):
    """Insert or replace information on a package into "table".
    "pkg" is assumed to be a dictionary in the form given by each package
    listed in the given repository."""
    # Assume package ID and URI exist.  Not much to do if they don't.
    id = pkg['id']
    uri = pkg['uri']

    # Assemble complete version number.
    # If any components are missing, set them to '0'.
    v = ['0','0','0','0','release']
    for i,j in enumerate(('major','minor','release','build','type')):
        try: v[i] = pkg['version'][j]
        except: pass
    # If type is "release", that's not very interesting, so ignore it.
    v = v if v[-1] != 'release' else v[:-1]
    version = '.'.join(v)

    # Get title and description.
    # First search for most preferred language available.
    l = dict()
    for lang in options.get_locale():
        try:
            l = pkg['localizations'][lang]
            break
        except: pass
    # Then get try to get title and description in that language.
    title=None; description=None
    try:
        title = l['title']
        description = l['description']
    except: pass

    # Straightforward optional fields.
    opt_field = {'info':None, 'size':None, 'md5':None, 'modified-time':None,
        'rating':None, 'vendor':None, 'icon':None}
    for i in opt_field.iterkeys():
        try: opt_field[i] = pkg[i]
        except: pass

    # Optional author information fields.
    author = {'name':None, 'website':None, 'email':None}
    for i in author.iterkeys():
        try: author[i] = pkg['author'][i]
        except: pass

    # Optional array fields.  Database cannot hold arrays, so combine all
    # elements of each array with a separator character.
    opt_list = {'previewpics':None, 'licenses':None, 'source':None,
        'categories':None}
    for i in opt_list.iterkeys():
        try: opt_list[i] = SEPCHAR.join(pkg[i])
        except: pass

    # Insert extracted data into table.
    cursor.execute("""Insert Or Replace Into "%s" Values
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""" % table,
        ( id,
        uri,
        version,
        title,
        description,
        opt_field['info'],
        opt_field['size'],
        opt_field['md5'],
        opt_field['modified-time'],
        opt_field['rating'],
        author['name'],
        author['website'],
        author['email'],
        opt_field['vendor'],
        opt_field['icon'],
        opt_list['previewpics'],
        opt_list['licenses'],
        opt_list['source'],
        opt_list['categories'],
        None, None ) )


def update_remote_url(url, cursor, full_update=None):
    """Adds database table for the repository held by the url object.
    full_update may be True (to force an update with the full repository),
    False (to force use of the updates-only URL, if available), or None (to
    select mode automatically)."""

    table = sanitize_sql(url)
    if table in (LOCAL_TABLE, REPO_INDEX_TABLE):
        raise RepoError(
            'Cannot handle a repo named "%s"; name is reserved for internal use.'
            % table)

    # Check if repo exists in index and has an updates URL.
    cursor.execute('''Select etag, last_modified, updates_url, last_update,
        last_full_update From "%s" Where url=?''' % REPO_INDEX_TABLE, (table,) )
    result = cursor.fetchone()

    if result is None:
        # This repo is not yet in the index (it's the first time it's
        # been checked), so make an empty entry and table for it.
        cursor.execute('''Insert Into "%s" (url,last_update,last_full_update)
            Values (?,?,?)''' % REPO_INDEX_TABLE, (table,0,0) )
        create_table(cursor, table)
        result = (None, None, None, 0, 0)
    etag, last_modified, updates_url, last_update, last_full_update = result
    # Ensure that the right substring is in updates_url.
    updates_url = updates_url if (isinstance(updates_url, basestring) and
        TIME_SUBSTRING in updates_url) else None

    # If autoselecting update mode, full update if it has been too long since
    # the last full update.  This is to periodically flush removed packages
    # from the database.
    t = int(time.time())
    if full_update is None:
        full_update = t - int(last_full_update) > FULL_UPDATE_TIME

    # Perform a standard full repository update.
    if full_update or not isinstance(updates_url, basestring):
        # Use conditional gets if it makes sense to do so.
        req = urllib2.Request(url, headers={} if full_update else {
            'If-None-Match':etag, 'If-Modified-Since':last_modified})

        class NotModifiedHandler(urllib2.BaseHandler):
            def http_error_304(self, req, fp, code, message, headers):
                return 304

        opener = urllib2.build_opener(NotModifiedHandler())
        try:
            url_handle = opener.open(req)
        except Exception as e:
            warnings.warn("Could not reach repo %s: %s" % (url, repr(e)))
            return

        # If no error, clear out old table for complete replacement.
        if url_handle != 304:
            cursor.execute('Drop Table If Exists "%s"' % table)
            create_table(cursor, table)

            # Parse JSON.
            # TODO: Is there any way to gracefully handle a malformed feed?
            repo = json.load(url_handle)

            # Parse each package in repo.
            for pkg in repo["packages"]:
                try: update_remote_package(table, pkg, cursor)
                except Exception as e:
                    warnings.warn("Could not process remote package: %s" % repr(e))

            # Now repo is all updated, let the index know its information.
            headers = url_handle.info()
            try: name = repo['repository']['name']
            except: name = None
            try: updates_url = repo['repository']['updates']
            except: updates_url = None
            cursor.execute('''Update "%s" Set name=?, etag=?, last_modified=?,
                updates_url=?, last_update=?, last_full_update=? Where url=?'''
                %REPO_INDEX_TABLE, (
                    name,
                    headers.getheader('ETag'),
                    headers.getheader('Last-Modified'),
                    updates_url,
                    t, t,
                    table) )

    # Get only changes since the last update.
    else:
        # Open updates URL with time of last update.
        url = updates_url.replace('%time%', str(last_update))
        try:
            url_handle = urllib2.urlopen(url)
        except Exception as e:
            warnings.warn("Could not reach update %s: %s" % (url, repr(e)))
            return

        t = int(time.time())
        # Parse JSON.
        # TODO: Is there any way to gracefully handle a malformed feed?
        repo = json.load(url_handle)

        # If any packages have been updated, parse them.
        if repo["packages"] is not None:
            for pkg in repo["packages"]:
                try: update_remote_package(table, pkg, cursor)
                except Exception as e:
                    warnings.warn("Could not process remote package: %s" % repr(e))

        # Now repo is all updated, let the index know its information.
        headers = url_handle.info()
        try: name = repo['repository']['name']
        except: name = None
        try: updates_url = repo['repository']['updates']
        except: updates_url = None
        cursor.execute('''Update "%s" Set name=?, updates_url=?, last_update=?
            Where url=?''' % REPO_INDEX_TABLE, (
                name,
                updates_url,
                t,
                table) )


def update_remote():
    """Adds a table for each repository to the database, adding an entry for each
    application listed in the repository."""
    # Open database connection.
    with sqlite3.connect(options.get_database()) as db:
        db.row_factory = sqlite3.Row
        c = db.cursor()

        for url in options.get_repos():
            try:
                update_remote_url(url, c)
            except Exception as e:
                warnings.warn("Could not process %s: %s" % (url, repr(e)))



def update_local_file(path, db_conn):
    """Adds an entry to the local database based on the PND found at "path"."""
    apps = libpnd.pxml_get_by_path(path)
    if not apps:
        raise ValueError("%s doesn't seem to be a real PND file." % path)

    m = md5()
    #with open(path, 'rb') as p:
    #    for chunk in iter(lambda: p.read(128*m.block_size), ''):
    #        m.update(chunk)

    # Extract all the useful information from the PND and add it to the table.
    # NOTE: libpnd doesn't yet have functions to look at the package element of
    # a PND.  Instead, extract the PXML and parse that element manually.
    pxml_buffer = ctypes.create_string_buffer(libpnd.PXML_MAXLEN)
    f = libpnd.libc.fopen(path, 'r')
    if not libpnd.pnd_seek_pxml(f):
        raise PNDError('PND file has no starting PXML tag.')
    if not libpnd.pnd_accrue_pxml(f, pxml_buffer, libpnd.PXML_MAXLEN):
        raise PNDError('PND file has no ending PXML tag.')

    try:
        # Strip extra trailing characters from the icon.  Remove them!
        end_tag = pxml_buffer.value.rindex('>')
        pxml = etree.XML(pxml_buffer.value[:end_tag+1])
        # Search for package element.
        pkg = pxml.find(xml_child('package'))
    except: pass

    # May need to fall back on first app element, assuming it's representative
    # of the package as a whole.
    app = apps[0]

    try:
        pkgid = pkg.attrib['id']
    except:
        pkgid = libpnd.pxml_get_unique_id(app)

    try:
        v = pkg.find(xml_child('version'))
        # TODO: Add support for 'type' attribute.
        # NOTE: Using attrib instead of get will be fragile on non standards-
        # compliant PNDs.
        version = '.'.join( (
            v.attrib['major'],
            v.attrib['minor'],
            v.attrib['release'],
            v.attrib['build'], ) )
    except:
        version = '.'.join( (
            str(libpnd.pxml_get_version_major(app)),
            str(libpnd.pxml_get_version_minor(app)),
            str(libpnd.pxml_get_version_release(app)),
            str(libpnd.pxml_get_version_build(app)), ) )

    try:
        author = pkg.find(xml_child('author'))
        author_name = author.get('name')
        author_website = author.get('website')
        author_email = author.get('email')
    except:
        author_name = libpnd.pxml_get_author_name(app)
        author_website = libpnd.pxml_get_author_website(app)
        author_email = None # NOTE: libpnd has no pxml_get_author_email?

    try:
        # Get title and description in the most preferred language available.
        titles = {}
        for t in pkg.find(xml_child('titles')):
            titles[t.attrib['lang']] = t.text
        for l in options.get_locale():
            try:
                title = titles[l]
                break
            except KeyError: pass
        title # Trigger NameError if it's not yet set.
    except:
        title = libpnd.pxml_get_app_name(app, options.get_locale()[0])

    try:
        descs = {}
        for d in pkg.find(xml_child('descriptions')):
            descs[d.attrib['lang']] = d.text
        for l in options.get_locale():
            try:
                description = descs[l]
                break
            except KeyError: pass
        description # Trigger NameError if it's not yet set.
    except:
        description = libpnd.pxml_get_description(app, options.get_locale()[0])

    try:
        icon = pkg.find(xml_child('icon')).get('src')
    except:
        icon = libpnd.pxml_get_icon(app)

    # Find out how many apps are in the PXML, so we can iterate over them.
    n_apps = 0
    for i in apps:
        if i is None: break
        n_apps += 1

    # Create the full list of contained applications.
    applications = SEPCHAR.join([ libpnd.pxml_get_unique_id( apps[i] )
        for i in xrange(n_apps) ])

    # Get all previewpics.  libpnd only supports two per application.
    previewpics = []
    for i in xrange(n_apps):
        p = libpnd.pxml_get_previewpic1( apps[i] )
        if p is not None: previewpics.append(p)
        p = libpnd.pxml_get_previewpic2( apps[i] )
        if p is not None: previewpics.append(p)
    if previewpics:
        previewpics = SEPCHAR.join(previewpics)
    else: previewpics = None

    # TODO: Get licenses and source urls once libpnd has that functionality.

    # Combine all categories in all apps.  libpnd supports two categories, each
    # with two subcategories in each app.  No effort is made to uniquify the
    # completed list.
    categories = []
    for i in xrange(n_apps):
        c = libpnd.pxml_get_main_category( apps[i] )
        if c is not None: categories.append(c)
        c = libpnd.pxml_get_subcategory1( apps[i] )
        if c is not None: categories.append(c)
        c = libpnd.pxml_get_subcategory2( apps[i] )
        if c is not None: categories.append(c)
        c = libpnd.pxml_get_altcategory( apps[i] )
        if c is not None: categories.append(c)
        c = libpnd.pxml_get_altsubcategory1( apps[i] )
        if c is not None: categories.append(c)
        c = libpnd.pxml_get_altsubcategory2( apps[i] )
        if c is not None: categories.append(c)
    if categories:
        categories = SEPCHAR.join(categories)
    else: categories = None

    # Output from libpnd gives encoded bytestrings, not Unicode strings.
    db_conn.text_factory = str
    db_conn.execute("""Insert Or Replace Into "%s" Values
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""" % LOCAL_TABLE,
        ( pkgid,
        path,
        version,
        title,
        description,
        None, # Likely no use for "info" on installed packages.
        None, # TODO: size field
        m.hexdigest(), # TODO: Just None?
        None, # TODO: modified_time field?
        None, # No use for "rating" either.
        author_name,
        author_website,
        author_email,
        None, # No use for "vendor" either.
        icon,
        previewpics,
        None, # TODO: Licenses once libpnd can pull them.
        None, # TODO: Sources once libpnd can pull them.
        categories,
        applications,
        None ) )

    # Clean up the pxml handle.
    for i in xrange(n_apps):
        libpnd.pxml_delete(apps[i])



def update_local():
    """Adds a table to the database, adding an entry for each application found
    in the searchpath."""
    # Open database connection.
    with sqlite3.connect(options.get_database()) as db:
        db.row_factory = sqlite3.Row
        # Create table from scratch to hold list of all installed PNDs.
        # Drops it first so no old entries get left behind.
        # TODO: Yes, there are probably more efficient ways than dropping
        # the whole thing, whatever, I'll get to it.
        db.execute('Drop Table If Exists "%s"' % LOCAL_TABLE)
        create_table(db, LOCAL_TABLE)

        # Find PND files on searchpath.
        searchpath = ':'.join(options.get_searchpath())
        search = libpnd.disco_search(searchpath, None)
        if not search:
            raise ValueError("Your install of libpnd isn't behaving right!  pnd_disco_search has returned null.")

        # If at least one PND is found, add each to the database.
        # Note that disco_search returns the path to each *application*.  PNDs with
        # multiple apps will therefore be returned multiple times.  Process any
        # such PNDs only once.
        n = libpnd.box_get_size(search)
        done = set()
        if n > 0:
            node = libpnd.box_get_head(search)
            path = libpnd.box_get_key(node)
            try: update_local_file(path, db)
            except Exception as e:
                warnings.warn("Could not process %s: %s" % (path, repr(e)))
            done.add(path)
            for i in xrange(n-1):
                node = libpnd.box_get_next(node)
                path = libpnd.box_get_key(node)
                if path not in done:
                    try: update_local_file(path, db)
                    except Exception as e:
                        warnings.warn("Could not process %s: %s" % (path, repr(e)))
                    done.add(path)
        db.commit()



# On import, this will execute, ensuring that necessary tables are created and
# can be depended upon to exist in later code.
with sqlite3.connect(options.get_database()) as db:
    # Index for all repositories to track important info.
    db.execute("""Create Table If Not Exists "%s" (
        url Text Primary Key, name Text, etag Text, last_modified Text,
        updates_url Text, last_update Text, last_full_update Text
        )""" % REPO_INDEX_TABLE)
    # Table of installed PNDs.
    create_table(db, LOCAL_TABLE)

    db.commit()
