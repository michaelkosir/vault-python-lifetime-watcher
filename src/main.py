from vault import Vault, AuthLifetimeWatcher, SecretsLifetimeWatcher

import asyncio


def printG(*args):
    print(' '.join(f"\033[92m {arg}\033[00m" for arg in args))


class DB:
    def __init__(self, username: str, password: str):
        self.username: str = username
        self.password: str = password

    def reload(self, data):
        self.username = data['username']
        self.password = data['password']


async def main():
    vault = Vault()

    # authenticate, start lifetime watcher, start/store task
    auth = vault.login()
    aw = AuthLifetimeWatcher(
        name="auth",
        vault=vault,
        secret=auth,
    )

    # pull dynamic secrets, start lifetime watcher, start/store task
    secret = vault.getDatabaseCredentials()
    db = DB(
        username=secret['data']['username'],
        password=secret['data']['password'],
    )
    sw = SecretsLifetimeWatcher(
        name="pgsql",
        vault=vault,
        secret=secret,
        newCredentials=vault.getDatabaseCredentials,
        onReload=db.reload,
    )

    # build relationship
    # as when an auth token is regenerated
    # secrets will need to be recreated/reloaded
    aw.watchers.append(sw)
    vault.watcher = aw

    # perform main async logic
    while True:
        printG(db.username, db.password, vault.client.token)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
