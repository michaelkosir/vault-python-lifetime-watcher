from typing import List, Callable
import asyncio
import random

import hvac


# testing purposes
with open("./roleid") as fp:
    ROLE_ID = fp.read()

with open("./secretid") as fp:
    SECRET_ID = fp.read()


def printR(m):
    print("\033[91m {}\033[00m".format(m))


def printY(m):
    print("\033[93m {}\033[00m".format(m))


class Vault:
    def __init__(self):
        self.client: hvac.Client = hvac.Client()
        self.role_id: str = ROLE_ID
        self.secret_id: str = SECRET_ID
        self.db_mount: str = "pgsql"
        self.db_role: str = "demo"
        self.watcher: LifetimeWatcher

    def login(self):
        printR(f"Authenticating via AppRole...")
        r = self.client.auth.approle.login(
            role_id=self.role_id,
            secret_id=self.secret_id
        )
        ttl = r['auth']['lease_duration']
        renewable = r['auth']['renewable']
        printR(f"Successfully authenticated via AppRole...")
        printR(f"Auth Token: TTL: {ttl} Renewable: {renewable}")
        return r

    def getDatabaseCredentials(self):
        printY(f"Fetching dynamic database credentials...")
        r = self.client.secrets.database.generate_credentials(
            name=self.db_role,
            mount_point=self.db_mount
        )
        ttl = r['lease_duration']
        renewable = r['renewable']
        printY(f"Successfully generated database credentials...")
        printY(f"Secret Lease Token: TTL: {ttl} Renewable: {renewable}")
        return r


class LifetimeWatcher:
    def __init__(self, name, vault, secret, threshold=0.70, jitter=0.05):
        self.name: str = name
        self.vault: Vault = vault
        self.secret: dict = secret
        self.threshold: float = threshold
        self.jitter: float = jitter
        self.interval: int
        self.task: asyncio.Task

    def _calculate_sleep_interval(self):
        jitter = random.uniform(-1 * self.jitter, self.jitter)
        if self.secret.get('auth'):
            return self.secret['auth']['lease_duration'] * (self.threshold + jitter)
        return self.secret['lease_duration'] * (self.threshold + jitter)

    async def _sleep(self):
        interval = self._calculate_sleep_interval()
        msg = f"{self.name}: Lifetime watcher sleeping for {interval:.2f}s..."
        _ = printR(msg) if self.name == "auth" else printY(msg)
        await asyncio.sleep(interval)


class AuthLifetimeWatcher(LifetimeWatcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.watchers: List[SecretsLifetimeWatcher] = []
        self.task = asyncio.create_task(self.start(), name=self.name)

    async def start(self):
        printR(f"Auth token lifetime watcher starting...")
        self.interval = self.secret['auth']['lease_duration']
        await self._manage_lifetime()

    async def _manage_lifetime(self):
        while True:
            if self.secret['auth']['renewable']:
                printR(f"{self.name}: Auth token is renewable, will perform renew after sleep...")
                await self._sleep()
                await self._manage_renewal()
            else:
                printR(f"{self.name}: Auth token is not renewable, will perform reauth after sleep...")
                await self._sleep()

            self.secret = self.vault.login()
            self.interval = self.secret['auth']['lease_duration']

            # now that we have a new auth token
            # we need to regenerate/reload credentials
            # as credentials are linked to the auth token
            await self._replace_watchers()

    async def _manage_renewal(self):
        while True:
            printR(f"{self.name}: Renewing auth token...")
            self.secret = self.vault.client.auth.token.renew_self()
            ttl = self.secret['auth']['lease_duration']
            renewable = self.secret['auth']['renewable']

            printR(f"{self.name}: Successfully renewed auth token...")
            printR(f"{self.name}: Auth Token: TTL: {ttl} Renewable: {renewable}")

            if self.secret['auth']['lease_duration'] < self.interval:
                printR(
                    f"{self.name}: Auth token approaching Max TTL, will perform reauth after sleep...")
                await self._sleep()
                return

            printR(f"{self.name}: Auth token is renewable, will perform renew after sleep...")
            await self._sleep()

    async def _replace_watchers(self):
        for w in self.watchers:
            async with w.lock:
                printY(f"{w.name}: Stopping outdated secret lease lifetime watcher")
                w.secret = w.newCredentials()
                w.reloadCredentials(w.secret['data'])
                w.task.cancel()
                w.task = asyncio.create_task(w.start())


class SecretsLifetimeWatcher(LifetimeWatcher):
    def __init__(self, newCredentials: Callable, onReload: Callable, **kwargs):
        super().__init__(**kwargs)
        self.newCredentials: Callable = newCredentials
        self.reloadCredentials: Callable | None = onReload
        self.lock = asyncio.Lock()
        self.task = asyncio.create_task(self.start(), name=self.name)

    async def start(self):
        printY(f"{self.name}: Secret lease lifetime watcher starting...")
        self.interval = self.secret['lease_duration']
        await self._manage_lifetime()

    async def _manage_lifetime(self):
        while True:
            if self.secret['renewable']:
                printY(f"{self.name}: Secret lease is renewable, will perform renew after sleep...")
                await self._sleep()
                await self._manage_renewal()
            else:
                printY(f"{self.name}: Secret lease is not renewable, will regenerate creds after sleep...")
                await self._sleep()

            async with self.lock:
                self.secret = self.newCredentials()
                self.reloadCredentials(self.secret['data'])
                self.interval = self.secret['lease_duration']

    async def _manage_renewal(self):
        while True:
            printY(f"{self.name}: Renewing secret lease...")
            secret = self.vault.client.sys.renew_lease(self.secret['lease_id'])
            secret['data'] = self.secret['data']
            self.secret = secret

            ttl = self.secret['lease_duration']
            renewable = self.secret['renewable']

            printY(f"{self.name}: Successfully renewed secret lease...")
            printY(f"{self.name}: Secret Lease: TTL: {ttl} Renewable: {renewable}")

            if self.secret['lease_duration'] < self.interval:
                printY(f"{self.name}: Secret lease approaching Max TTL, will regenerate creds after sleep...")
                await self._sleep()
                return

            printY(f"{self.name}: Secret lease is renewable, will perform renew after sleep...")
            await self._sleep()
