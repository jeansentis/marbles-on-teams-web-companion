import tempfile
import unittest
from pathlib import Path
from unittest import mock

import companion


SNAPSHOT_ID = "bf2e1969-4e24-1c94-7c2a-20913359a639"
RACE = "\n".join([
    "SnapshotId,Position,Username,DisplayName,Platform,NameColorHex,"
    "SeasonPointsEarned,SeasonPointsTotal,SeasonWinsTotal,"
    "SeasonMatchesPlayedTotal,TimeInRaceSeconds,Eliminated",
    f"{SNAPSHOT_ID},1,winner,Winner,Twitch,FFFFFFFF,86,218,3,4,145.006,false",
])
SUMMARY = "\n".join([
    "SchemaVersion,SnapshotId,GeneratedAtUtc,Status,GameMode,SessionType,"
    "MapName,MapCreator,PlayerCount,FinishedCount,EliminatedCount,"
    "WinnerPlatform,WinnerUsername",
    f"4,{SNAPSHOT_ID},2026-08-04T16:41:29Z,Final,Race,Qualifying,"
    "The Dojo,Pixel by Pixel Studios,1,1,0,Twitch,winner",
])


class SummaryPairingTests(unittest.TestCase):
    def test_v4_result_waits_for_matching_final_summary(self):
        self.assertIsNone(companion._matching_summary(RACE, ""))
        self.assertIsNone(
            companion._matching_summary(RACE, SUMMARY.replace(SNAPSHOT_ID, "stale"))
        )
        self.assertIsNone(
            companion._matching_summary(RACE, SUMMARY.replace("Final", "Running"))
        )
        self.assertEqual(SUMMARY, companion._matching_summary(RACE, SUMMARY))

    def test_legacy_result_does_not_require_summary(self):
        legacy = "\n".join([
            "Position,Username,Displayname,NameColor,PointsEarned,FinishTime,Eliminated",
            "1,winner,Winner,FFFFFFFF,42,12.345,false",
        ])
        self.assertEqual("", companion._matching_summary(legacy, ""))

    def test_watcher_queues_result_and_summary_together(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            companion.Watcher, "_load_outbox", return_value=[]
        ):
            folder = Path(tmp)
            (folder / "LastSeasonRace.csv").write_text(RACE, encoding="utf-8")
            (folder / "LastSeasonRaceSummary.csv").write_text(
                SUMMARY.replace(SNAPSHOT_ID, "stale"), encoding="utf-8"
            )
            watcher = companion.Watcher(
                folder, "https://example.invalid", "caster", lambda *_: None
            )
            queued = []
            watcher._enqueue = lambda kind, payload: queued.append((kind, payload))

            self.assertEqual("wait", watcher._try_queue_result("race"))
            self.assertEqual([], queued)

            (folder / "LastSeasonRaceSummary.csv").write_text(
                SUMMARY, encoding="utf-8"
            )
            self.assertEqual("queued", watcher._try_queue_result("race"))
            self.assertEqual("race", queued[0][0])
            self.assertEqual(
                RACE.splitlines(), queued[0][1]["csv_content"].splitlines()
            )
            self.assertEqual(
                SUMMARY.splitlines(),
                queued[0][1]["summary_csv_content"].splitlines(),
            )
            self.assertEqual("", queued[0][1]["map_csv_content"])


if __name__ == "__main__":
    unittest.main()
