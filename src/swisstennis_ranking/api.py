"""Swiss Tennis GraphQL client (hasura.swisstennis.ch) with mytennis.ch login."""

import json
import os
import threading
import time

import requests
from dotenv import load_dotenv

GRAPHQL_URL = "https://hasura.swisstennis.ch/v1/graphql"
LOGIN_URL = "https://www.mytennis.ch/fr"
TOKEN_URL_PREFIX = (
    "https://swisstennisch.b2clogin.com/swisstennisch.onmicrosoft.com/"
    "b2c_1a_b2c_1_signup-signin/oauth2/v2.0/token"
)

PLAYERS_QUERY = """
query PlayerList($offset: Int, $limit: Int, $where: lizenz_nehmer_bool_exp) {
  lizenz_nehmer(offset: $offset, limit: $limit, where: $where, order_by: [{licenceNumber: asc}]) {
    licenceNumber
    classification
    classificationValue
    competitionValue
    ageCategoryRedundant
    ranking
    kontingent
    person { id firstname lastname gender }
  }
}"""

HISTORY_QUERY = """
query playerRankingHistory($id: Int!, $from: timestamp) {
  list: RankingHistory(where: {personId: {_eq: $id}, date: {_gte: $from}}, order_by: {date: asc}) {
    personId
    firstname
    lastname
    date
    classification
    rank
    competitionValue
    classificationValue
    games
  }
}"""

# Per published list: lzh_kontingent is 1 for players inside the category quota and 0 for players
# outside it (foreigners, Art. 9.2), who carry assigned values instead of computed ones.
LIST_FLAGS_QUERY = """
query listFlags($date: timestamp, $offset: Int, $limit: Int) {
  list: RankingHistory(where: {date: {_eq: $date}}, order_by: {personId: asc}, offset: $offset, limit: $limit) {
    personId
    kontingent: lzh_kontingent
  }
}"""

# Players with a result dated on or after $from (to update an existing download).
RECENT_PLAYERS_QUERY = """
query recentPlayers($from: timestamp, $offset: Int, $limit: Int) {
  results: AllSingleResults(
    where: {date: {_gte: $from}}, distinct_on: playerPersonId, order_by: {playerPersonId: asc},
    offset: $offset, limit: $limit
  ) {
    playerPersonId
  }
}"""

CURRENT_SEASON_QUERY = "query { RankSeasonRange(where: {seasonPointer: {_eq: 1}}) { dateBegin } }"

RESULTS_QUERY = """
query SingleResults($where: AllSingleResults_bool_exp, $limit: Int) {
  results: AllSingleResults(where: $where, order_by: {date: asc}, limit: $limit) {
    date
    tournamentName
    adversaryPersonId
    adversaryFirstname
    adversaryLastname
    playerSet1WonGames
    adversarySet1WonGames
    playerSet2WonGames
    adversarySet2WonGames
    playerSet3WonGames
    adversarySet3WonGames
    playerWinnerCode
    adversaryValue: Asr_AdversaryValue_f
    encounterId: Asr_Id_Item_l
    matchNotConsidered
    source
    rank: Asr_RankConvert_s
  }
}"""


def login() -> str:
    """Log in to mytennis.ch with headless Chrome and return the B2C id_token.

    Credentials come from MYTENNIS_USER / MYTENNIS_PASSWORD (environment or .env).
    The token is read from the browser's network log instead of a proxy.
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as ec
    from selenium.webdriver.support.ui import WebDriverWait

    load_dotenv()
    user, password = os.environ.get("MYTENNIS_USER"), os.environ.get("MYTENNIS_PASSWORD")
    if not user or not password:
        raise RuntimeError("Set MYTENNIS_USER and MYTENNIS_PASSWORD in the environment or .env")

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    # needed in a container (no user namespaces, small /dev/shm)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    # the Docker image uses the distribution's Chromium and driver (else Selenium Manager finds one)
    if binary := os.environ.get("CHROME_BIN"):
        options.binary_location = binary
    service = webdriver.ChromeService(executable_path=os.environ.get("CHROMEDRIVER"))
    driver = webdriver.Chrome(options=options, service=service)
    try:
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 30)
        wait.until(ec.presence_of_element_located((By.ID, "UserId"))).send_keys(user)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.ID, "next").click()

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for entry in driver.get_log("performance"):
                msg = json.loads(entry["message"])["message"]
                if (
                    msg["method"] == "Network.responseReceived"
                    and msg["params"]["response"]["url"].startswith(TOKEN_URL_PREFIX)
                    and msg["params"]["response"]["status"] == 200
                ):
                    body = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": msg["params"]["requestId"]}
                    )
                    return json.loads(body["body"])["id_token"]
            time.sleep(0.5)
        raise RuntimeError("Login did not produce a token (wrong credentials or changed login page?)")
    finally:
        driver.quit()


class Client:
    """Thread-safe GraphQL client that logs in again when the token expires."""

    def __init__(self, token: str | None = None):
        self._token = token or login()
        self._lock = threading.Lock()
        self._local = threading.local()

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def _refresh(self, stale: str) -> None:
        with self._lock:
            if self._token == stale:  # another thread may already have refreshed
                self._token = login()

    def query(self, query: str, variables: dict, retries: int = 3) -> dict:
        for attempt in range(retries + 1):
            token = self._token
            try:
                resp = self._session().post(
                    GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                    headers={"authorization": f"Bearer {token}"},
                    timeout=60,
                )
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(2**attempt)
                continue
            if resp.status_code in (401, 403):
                self._refresh(token)
                continue
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            body = resp.json()
            if errors := body.get("errors"):
                if any("JWT" in e.get("message", "") for e in errors) and attempt < retries:
                    self._refresh(token)
                    continue
                raise RuntimeError(f"GraphQL error: {errors}")
            return body["data"]
        raise RuntimeError("GraphQL request failed after retries")
