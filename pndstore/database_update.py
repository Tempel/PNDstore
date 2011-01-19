import options, urllib2, sqlite3, json, ctypes

#This module currently only supports version 1.0 of the PND repository
#specification as seen at http://pandorawiki.org/PND_repository_specification
REPO_VERSION = 1.0

LOCAL_TABLE = 'local'

class RepoError(Exception): pass


def sanitize_sql(name):
    """The execute method's parametrization does not work for table names.  Therefore string formatting must be used, which bypasses the sqlite3 package's sanitization.  This function takes a stab at sanitizing.  It's not perfect, but it's also not a huge issue here; if you're reading a malicious feed, there are worse things they can do than screw with this database."""
    return str(name).translate(None, """.,;:'"(){}-""")


def create_table(cursor, name):
    name = sanitize_sql(name)
    cursor.execute("""Create Table If Not Exists "%s" (
        id Primary Key,
        version_major Int Not Null,
        version_minor Int Not Null,
        version_release Int Not Null,
        version_build Int Not Null,
        uri Not Null,
        title Not Null,
        description Not Null,
        author,
        vendor,
        icon,
        icon_cache Buffer
        )""" % name)
    #TODO: Holy crap!  Forgot categories!


def open_repos():
    return [urllib2.urlopen(i) for i in options.get_repos()]


def update_remote():
    """Adds a table for each repository to the database, adding an entry for each application listed in the repository."""
    #Open database connection.
    db = sqlite3.connect(options.get_database())
    db.row_factory = sqlite3.Row
    c = db.cursor()
    repos = open_repos()
    try:
        for i in repos:

            #Parse JSON.
            #TODO: Is there any way to gracefully handle a malformed feed?
            try: repo = json.load(i)
            except ValueError:
                raise RepoError('Malformed JSON file from %s'%i.geturl())

            try: 
                #Check it's the right version.
                v = repo["repository"]["version"]
                if v != REPO_VERSION:
                    raise RepoError('Incorrect repository version (required %f, got %f)'
                        % (REPO_VERSION, v))

                #Create table from scratch for this repo.
                #Drops it first so no old entries get left behind.
                #TODO: Yes, there are probably more efficient ways than
                #dropping the whole thing, whatever, I'll get to it.
                table = sanitize_sql(repo["repository"]["name"])
                if table == LOCAL_TABLE:
                    raise RepoError('Cannot handle a repo named "%s"; name is reserved for internal use.'%LOCAL_TABLE)
                c.execute('Drop Table If Exists "%s"' % table)
                create_table(c, table)

                #Insert Or Replace for each app in repo.
                #TODO: Break into subfunctions?
                for app in repo["applications"]:

                    #Get info in preferred language (fail if none available).
                    title=None; description=None
                    for lang in options.get_locale():
                        try:
                            title = app['localizations'][lang]['title']
                            description = app['localizations'][lang]['description']
                            break
                        except KeyError: pass
                    if title is None or description is None:
                        raise RepoError('An application does not have any usable language')

                    #These fields will not be present for every app.
                    opt_field = {'author':None, 'vendor':None, 'icon':None}
                    for i in opt_field.iterkeys():
                        try: opt_field[i] = app[i]
                        except KeyError: pass

                    c.execute("""Insert Or Replace Into "%s" Values
                        (?,?,?,?,?,?,?,?,?,?,?,?)""" % table,
                        ( app['id'],
                        app['version']['major'],
                        app['version']['minor'],
                        app['version']['release'],
                        app['version']['build'],
                        app['uri'],
                        title,
                        description,
                        opt_field['author'],
                        opt_field['vendor'],
                        opt_field['icon'], None) )
                    #TODO: Holy crap!  Forgot categories!
                    #TODO: make sure no required fields are missing. covered by try and Not Null?
                    #TODO: Don't erase icon_cache if icon hasn't changed.

            except KeyError:
                raise RepoError('A required field is missing from this repository')
                #TODO: Make it indicate which field that is?

    finally:
        for i in repos: i.close()
        db.commit()
        c.close()


def update_local():
    #Useful libpnd functions:
    #pnd_apps.h: get_appdata_path for when we want a complete removal
    #   (this will be needed elsewhere later)
    #pnd_conf.h: pnd_conf_query_searchpath if we happen to need libpnd configs
    #pnd_desktop.h: pnd_emit_icon_to_buffer to get an icon for caching.
    #   pnd_map_dotdesktop_categories ?
    #pnd_discovery.h: pnd_disco_search gives list of valid apps.  PERFECT.
    #pnd_locate.h: pnd_locate_filename for finding path of specific PND.
    #pnd_notify.h: everything in here for watching for file changes.
    #   or perhaps use dbus as per pnd_dbusnotify.h
    #pnd_pndfiles.h: pnd_pnd_mount for getting screenshots from within.
    #all of pnd_pxml.h for information from a PXML.
    #pnd_tinyxml.h: pnd_pxml_parse for exactly what it says.
    pass
