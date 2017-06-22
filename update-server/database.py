from util import get_hash
from config import Config
import pyodbc
import logging

class Database():
    def __init__(self):
        # python3 immport pyodbc; pyodbc.drivers()
        #self.cnxn = pyodbc.connect("DRIVER={SQLite3};SERVER=localhost;DATABASE=test.db;Trusted_connection=yes")
        self.log = logging.getLogger(__name__)
        self.config = Config()
        connection_string = "DRIVER={};SERVER=localhost;DATABASE={};UID={};PWD={};PORT={}".format(
                self.config.get("database_type"), self.config.get("database_name"), self.config.get("database_user"), 
                self.config.get("database_pass"), self.config.get("database_port"))
        self.cnxn = pyodbc.connect(connection_string)
        self.c = self.cnxn.cursor()
        self.log.debug("connected to databse")

    def commit(self):
        self.cnxn.commit()
        self.log.debug("database commit")

    def create_tables(self):
        self.log.info("creating tables")
        with open('tables.sql') as t:
            self.c.execute(t.read())
        self.commit()
        self.log.info("created tables")

    def insert_release(self, distro, release):
        self.log.info("insert %s/%s ", distro, release)
        sql = "INSERT INTO releases VALUES (?, ?) ON CONFLICT DO NOTHING;"
        self.c.execute(sql, distro, release)
        self.commit()

    def insert_supported(self, distro, release, target, subtarget="%"):
        self.log.info("insert supported {} {} {} {}".format(distro, release, target, subtarget))
        sql = """UPDATE targets SET supported = true
            WHERE 
                distro=? AND 
                release=? AND 
                target=? AND 
                subtarget LIKE ?"""
        self.c.execute(sql, distro, release, target, subtarget)
        self.commit()

    def get_releases(self, distro=None):
        if not distro:
            return self.c.execute("select * from releases").fetchall()
        else:
            releases = self.c.execute("select release from releases WHERE distro=?", (distro, )).fetchall()
            respond = []
            for release in releases:
                respond.append(release[0])
            return respond

    def insert_hash(self, hash, packages):
        sql = """INSERT INTO packages_hashes
            VALUES (?, ?)
            ON CONFLICT DO NOTHING;"""
        self.c.execute(sql, (hash, " ".join(packages)))
        self.commit()

    def insert_profiles(self, distro, release, target, subtarget, profiles_data):
        self.log.debug("insert_profiels %s/%s/%s/%s", distro, release, target, subtarget)
        default_packages, profiles = profiles_data
        sql = "INSERT INTO profiles VALUES (?, ?, ?, ?, ?, ?, ?)"
        for profile in profiles:
            self.c.execute(sql, distro, release, target, subtarget, *profile)
        self.c.execute("INSERT INTO default_packages VALUES (?, ?, ?, ?, ?)", distro, release, target, subtarget, default_packages)
        self.commit()

    def check_profile(self, distro, release, target, subtarget, profile):
        self.log.debug("check_profile %s/%s/%s/%s/s", distro, release, target, subtarget, profile)
        self.c.execute("""SELECT EXISTS(
            SELECT 1 FROM profiles
            WHERE 
                distro=? AND 
                release=? AND 
                target=? AND 
                subtarget = ? AND 
                (name = ? OR board = ?)
            LIMIT 1);""",
            distro, release, target, subtarget, profile, profile)
        if self.c.fetchone()[0]:
            return True
        return False

    def get_default_packages(self, distro, release, target, subtarget):
        self.log.debug("get_default_packages for %s/%s", target, subtarget)
        self.c.execute("""SELECT packages
            FROM default_packages
            WHERE 
                distro=? AND 
                release=? AND 
                target=? AND 
                subtarget=?;""", 
            distro, release, target, subtarget)
        response = self.c.fetchone()
        if response:
            return response[0].split(" ")
        return response

    def insert_packages(self, distro, release, target, subtarget, packages):
        self.log.info("insert packages of %s/%s ", target, subtarget)
        sql = "INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?)"
        for package in packages:
            # (name, version)
            self.c.execute(sql, distro, release, target, subtarget, *package)
        self.commit()

    def get_available_packages(self, distro, release, target, subtarget):
        self.log.debug("get_available_packages for %s/%s/%s/%s", distro, release, target, subtarget)
        self.c.execute("""SELECT name, version
            FROM packages 
            WHERE 
                distro=? AND 
                release=? AND 
                target=? AND 
                subtarget=?;""", 
            distro, release, target, subtarget)
        response = {}
        for name, version in self.c.fetchall():
            response[name] = version 
        return response
    
    def insert_target(self, distro, release, target, subtargets):
        self.log.info("insert %s/%s ", target, " ".join(subtargets))
        sql = "INSERT INTO targets VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING;"
        for subtarget in subtargets:
            self.c.execute(sql, distro, release, target, subtarget)

        self.commit()

    def get_targets(self, distro, release):
        return self.c.execute("""SELECT target, subtarget FROM targets
            WHERE distro=? AND release=?""", 
            (distro, release, )).fetchall()

    def check_target(self, distro, release, target, subtarget):
        self.log.debug("check for %s/%s/%s/%s", distro, release, target, subtarget)
        self.c.execute("""SELECT EXISTS(
            SELECT 1 FROM targets 
            WHERE 
                distro=? AND 
                release=? AND 
                target=? AND 
                subtarget=?
            LIMIT 1);""",
            distro, release, target, subtarget)
        if self.c.fetchone()[0] != "0":
            return True
        else:
            self.log.warning("check fail for %s/%s/%s/%s", distro, release,  target, subtarget)
            return False

    def add_build_job(self, image):
        sql = """INSERT INTO build_queue
            (image_hash, distro, release, target, subtarget, profile, packages, network_profile)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) 
            ON CONFLICT (image_hash) DO UPDATE
            SET id = build_queue.id
            RETURNING id, status;"""
        image_array = image.as_array()
        self.c.execute(sql, (get_hash(" ".join(image_array), 12), *image_array))
        self.commit()
        if self.c.description:
            return self.c.fetchone()
        else:
            return None

    def get_build_job(self):
        sql = """UPDATE build_queue
            SET status = 1
            WHERE status = 0 AND id = (
                SELECT MIN(id)
                FROM build_queue
                WHERE status = 0
                )
            RETURNING * ;"""
        self.c.execute(sql)
        if self.c.description:
            self.commit()
            return self.c.fetchone()
        else:
            return None

    def set_build_job_fail(self, image_request_hash):
        sql = """UPDATE build_queue
            SET status = 2
            WHERE image_hash = ?;"""
        self.c.execute(sql, (image_request_hash, ))
        self.commit()

    def del_build_job(self, image_request_hash):
        sql = """DELETE FROM build_queue
            WHERE image_hash = ?;"""
        self.c.execute(sql, (image_request_hash, ))
        self.commit()
        
if __name__ == "__main__":
    db = Database()
    db.create_tables()
