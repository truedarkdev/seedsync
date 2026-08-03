# Copyright 2017, Inderpreet Singh, All rights reserved.

import unittest
import os
import tempfile

from common import Config, ConfigError, PersistError
from common.config import InnerConfig, Checkers, Converters


class TestConverters(unittest.TestCase):
    def test_int(self):
        self.assertEqual(0, Converters.int(None, "", "0"))
        self.assertEqual(1, Converters.int(None, "", "1"))
        self.assertEqual(-1, Converters.int(None, "", "-1"))
        self.assertEqual(5000, Converters.int(None, "", "5000"))
        self.assertEqual(-5000, Converters.int(None, "", "-5000"))
        with self.assertRaises(ConfigError) as e:
            Converters.int(TestConverters, "bad", "")
        self.assertEqual("Bad config: TestConverters.bad is empty", str(e.exception))
        with self.assertRaises(ConfigError) as e:
            Converters.int(TestConverters, "bad", "3.14")
        self.assertEqual("Bad config: TestConverters.bad (3.14) must be an integer value", str(e.exception))
        with self.assertRaises(ConfigError) as e:
            Converters.int(TestConverters, "bad", "cat")
        self.assertEqual("Bad config: TestConverters.bad (cat) must be an integer value", str(e.exception))

    def test_bool(self):
        self.assertEqual(True, Converters.bool(None, "", "True"))
        self.assertEqual(False, Converters.bool(None, "", "False"))
        self.assertEqual(True, Converters.bool(None, "", "true"))
        self.assertEqual(False, Converters.bool(None, "", "false"))
        self.assertEqual(True, Converters.bool(None, "", "TRUE"))
        self.assertEqual(False, Converters.bool(None, "", "FALSE"))
        self.assertEqual(True, Converters.bool(None, "", "1"))
        self.assertEqual(False, Converters.bool(None, "", "0"))
        with self.assertRaises(ConfigError) as e:
            Converters.bool(TestConverters, "bad", "")
        self.assertEqual("Bad config: TestConverters.bad is empty", str(e.exception))
        with self.assertRaises(ConfigError) as e:
            Converters.bool(TestConverters, "bad", "cat")
        self.assertEqual("Bad config: TestConverters.bad (cat) must be a boolean value", str(e.exception))
        with self.assertRaises(ConfigError) as e:
            Converters.bool(TestConverters, "bad", "-3.14")
        self.assertEqual("Bad config: TestConverters.bad (-3.14) must be a boolean value", str(e.exception))

    def test_bool_accepts_distutils_compat_values(self):
        self.assertEqual(True, Converters.bool(None, "", "yes"))
        self.assertEqual(False, Converters.bool(None, "", "off"))


class DummyInnerConfig(InnerConfig):
    c_prop1 = InnerConfig._create_property("prop1", Checkers.null, Converters.null)
    a_prop2 = InnerConfig._create_property("prop2", Checkers.null, Converters.null)
    b_prop3 = InnerConfig._create_property("prop3", Checkers.null, Converters.null)

    def __init__(self):
        self.c_prop1 = "1"
        self.a_prop2 = "2"
        self.b_prop3 = "3"


class DummyInnerConfig2(InnerConfig):
    prop_int = InnerConfig._create_property("prop_int", Checkers.null, Converters.int)
    prop_str = InnerConfig._create_property("prop_str", Checkers.string_nonempty, Converters.null)

    def __init__(self):
        self.prop_int = None
        self.prop_str = None


class TestInnerConfig(unittest.TestCase):
    def test_property_order(self):
        dummy_config = DummyInnerConfig()
        self.assertEqual(["c_prop1", "a_prop2", "b_prop3"], list(dummy_config.as_dict().keys()))

    def test_has_property(self):
        dummy_config = DummyInnerConfig()
        self.assertTrue(dummy_config.has_property("c_prop1"))
        self.assertTrue(dummy_config.has_property("a_prop2"))
        self.assertTrue(dummy_config.has_property("b_prop3"))
        self.assertFalse(dummy_config.has_property("not_prop"))
        self.assertFalse(dummy_config.has_property("__init__"))
        self.assertFalse(dummy_config.has_property(""))

    def test_checker_is_called(self):
        dummy_config = DummyInnerConfig2()
        dummy_config.prop_str = "a string"
        self.assertEqual("a string", dummy_config.prop_str)
        with self.assertRaises(ConfigError) as e:
            dummy_config.prop_str = ""
        self.assertEqual("Bad config: DummyInnerConfig2.prop_str is empty", str(e.exception))

    def test_converter_is_called(self):
        dummy_config = DummyInnerConfig2.from_dict({"prop_int": "5", "prop_str": "a"})
        self.assertEqual(5, dummy_config.prop_int)
        with self.assertRaises(ConfigError) as e:
            DummyInnerConfig2.from_dict({"prop_int": "cat", "prop_str": "a"})
        self.assertEqual("Bad config: DummyInnerConfig2.prop_int (cat) must be an integer value", str(e.exception))


class TestConfig(unittest.TestCase):
    def test_lftp_legacy_password_argv_defaults_false_and_backfills(self):
        defaults = Config.Lftp()
        legacy_dict = defaults.as_dict()
        del legacy_dict["use_legacy_lftp_password_argv"]

        loaded = Config.Lftp.from_dict(legacy_dict)

        self.assertFalse(defaults.use_legacy_lftp_password_argv)
        self.assertFalse(loaded.use_legacy_lftp_password_argv)

    def __check_unknown_error(self, cls, good_dict):
        """
        Helper method to check that a config class raises an error on
        an unknown key
        :param cls:
        :param good_dict:
        :return:
        """
        bad_dict = dict(good_dict)
        bad_dict["unknown"] = "how did this get here"
        with self.assertRaises(ConfigError) as error:
            cls.from_dict(bad_dict)
        self.assertTrue(str(error.exception).startswith("Unknown config"))

    def __check_missing_error(self, cls, good_dict, key):
        """
        Helper method to check that a config class raises an error on
        a missing key
        :param cls:
        :param good_dict:
        :param key:
        :return:
        """
        bad_dict = dict(good_dict)
        del bad_dict[key]
        with self.assertRaises(ConfigError) as error:
            cls.from_dict(bad_dict)
        self.assertTrue(str(error.exception).startswith("Missing config"))

    def __check_empty_error(self, cls, good_dict, key):
        """
        Helper method to check that a config class raises an error on
        a empty value
        :param cls:
        :param good_dict:
        :param key:
        :return:
        """
        bad_dict = dict(good_dict)
        bad_dict[key] = ""
        with self.assertRaises(ConfigError) as error:
            cls.from_dict(bad_dict)
        self.assertTrue(str(error.exception).startswith("Bad config"))
        bad_dict[key] = "   "
        with self.assertRaises(ConfigError) as error:
            cls.from_dict(bad_dict)
        self.assertTrue(str(error.exception).startswith("Bad config"))

    def check_common(self, cls, good_dict, keys):
        """
        Helper method to run some common checks
        :param cls:
        :param good_dict:
        :param keys:
        :return:
        """
        # unknown
        self.__check_unknown_error(cls, good_dict)

        for key in keys:
            # missing key
            self.__check_missing_error(cls, good_dict, key)
            # empty value
            self.__check_empty_error(cls, good_dict, key)

    def check_bad_value_error(self, cls, good_dict, key, value):
        """
        Helper method to check that a config class raises an error on
        a bad value
        :param cls:
        :param good_dict:
        :param key:
        :param value:
        :return:
        """
        bad_dict = dict(good_dict)
        bad_dict[key] = value
        with self.assertRaises(ConfigError) as error:
            cls.from_dict(bad_dict)
        self.assertTrue(str(error.exception).startswith("Bad config"))

    def test_has_section(self):
        config = Config()
        self.assertTrue(config.has_section("general"))
        self.assertTrue(config.has_section("lftp"))
        self.assertTrue(config.has_section("validate"))
        self.assertTrue(config.has_section("controller"))
        self.assertTrue(config.has_section("web"))
        self.assertTrue(config.has_section("autoqueue"))
        self.assertTrue(config.has_section("logging"))
        self.assertTrue(config.has_section("notifications"))
        self.assertFalse(config.has_section("nope"))
        self.assertFalse(config.has_section("from_file"))
        self.assertFalse(config.has_section("__init__"))

    def test_legacy_general_webhook_secret_migrates_without_loss(self):
        source = Config().as_dict()
        source["General"]["webhook_secret"] = "legacy-signing-secret"
        source.pop("Notifications")

        migrated = Config.from_dict(source)

        self.assertEqual("legacy-signing-secret", migrated.notifications.hmac_secret)
        self.assertNotIn("webhook_secret", migrated.general.as_dict())

    def test_existing_notification_config_defaults_provider_to_webhook(self):
        source = Config().as_dict()
        source["Notifications"].pop("provider")
        source["Notifications"].pop("apprise_url")
        source["Notifications"].pop("apprise_tag")

        migrated = Config.from_dict(source)

        self.assertEqual("webhook", migrated.notifications.provider)
        self.assertEqual("", migrated.notifications.apprise_url)
        self.assertEqual("", migrated.notifications.apprise_tag)

    def test_apprise_notification_config_round_trips(self):
        config = Config()
        config.notifications.provider = "apprise"
        config.notifications.apprise_url = "https://apprise.example.test/notify/key"
        config.notifications.apprise_tag = "seedbox"

        restored = Config.from_dict(config.as_dict())

        self.assertEqual("apprise", restored.notifications.provider)
        self.assertEqual("https://apprise.example.test/notify/key", restored.notifications.apprise_url)
        self.assertEqual("seedbox", restored.notifications.apprise_tag)

    def test_notification_provider_rejects_unknown_values(self):
        config = Config()
        with self.assertRaises(ConfigError):
            config.notifications.provider = "discord"

    def test_general(self):
        good_dict = {
            "log_level": "DEBUG",
            "verbose": "False",
            "exclude_patterns": "*.nfo,Sample/",
            "api_token": "token-value",
            "allowed_hostname": "",
            "trusted_browser_bootstrap_remote_addrs": "172.25.0.1/32",
            "browser_handover_recovery_version": "2026.04.03",
            "disable_browser_auth": "True",
            "breadcrumb_trace_enabled": "False",
            "breadcrumb_trace_retention_depth": "128",
            "config_api_redact_remote_details": "False",
        }
        general = Config.General.from_dict(good_dict)
        self.assertEqual("DEBUG", general.log_level)
        self.assertEqual(False, general.verbose)
        self.assertEqual("*.nfo,Sample/", general.exclude_patterns)
        self.assertEqual("token-value", general.api_token)
        self.assertEqual("", general.allowed_hostname)
        self.assertFalse(general.has_property("trusted_browser_bootstrap_remote_addrs"))
        self.assertEqual("2026.04.03", general.browser_handover_recovery_version)
        self.assertTrue(general.disable_browser_auth)
        self.assertEqual(False, general.breadcrumb_trace_enabled)
        self.assertEqual(128, general.breadcrumb_trace_retention_depth)
        self.assertEqual(False, general.config_api_redact_remote_details)

        self.check_common(Config.General,
                          good_dict,
                          {
                              "verbose"
                          })

        # bad values
        self.check_bad_value_error(Config.General, good_dict, "log_level", "SomeString")
        self.check_bad_value_error(Config.General, good_dict, "log_level", "-1")
        self.check_bad_value_error(Config.General, good_dict, "verbose", "SomeString")
        self.check_bad_value_error(Config.General, good_dict, "verbose", "-1")
        self.check_bad_value_error(Config.General, good_dict, "disable_browser_auth", "2")
        self.check_bad_value_error(Config.General, good_dict, "breadcrumb_trace_retention_depth", "")
        self.check_bad_value_error(Config.General, good_dict, "breadcrumb_trace_retention_depth", "0")
        self.check_bad_value_error(Config.General, good_dict, "breadcrumb_trace_retention_depth", "1025")

    def test_general_defaults_log_level_to_info_when_missing(self):
        general = Config.General.from_dict({
            "verbose": "False",
            "api_token": "token-value",
            "allowed_hostname": "",
            "trusted_browser_bootstrap_remote_addrs": "172.25.0.1/32",
            "browser_handover_recovery_version": "2026.04.03",
            "breadcrumb_trace_enabled": "False",
            "config_api_redact_remote_details": "False",
        })

        self.assertEqual("INFO", general.log_level)

    def test_general_defaults_exclude_patterns_to_empty_string_when_missing(self):
        general = Config.General.from_dict({
            "log_level": "DEBUG",
            "verbose": "False",
            "api_token": "token-value",
            "allowed_hostname": "",
            "trusted_browser_bootstrap_remote_addrs": "172.25.0.1/32",
            "browser_handover_recovery_version": "2026.04.03",
            "breadcrumb_trace_enabled": "False",
            "config_api_redact_remote_details": "False",
        })

        self.assertEqual("", general.exclude_patterns)

    def test_general_legacy_debug_maps_to_log_level(self):
        for debug_value, expected_level in ((True, "DEBUG"), (False, "INFO")):
            with self.subTest(debug_value=debug_value):
                general = Config.General.from_dict({
                    "debug": str(debug_value),
                    "verbose": "False",
                    "api_token": "token-value",
                    "allowed_hostname": "",
                    "trusted_browser_bootstrap_remote_addrs": "172.25.0.1/32",
                    "browser_handover_recovery_version": "2026.04.03",
                    "breadcrumb_trace_enabled": "False",
                    "config_api_redact_remote_details": "False",
                })

                self.assertEqual(expected_level, general.log_level)

    def test_general_debug_alias_updates_log_level(self):
        general = Config.General()

        self.assertTrue(general.has_property("debug"))

        general.set_property("debug", True)
        self.assertEqual("DEBUG", general.log_level)

        general.set_property("debug", False)
        self.assertEqual("INFO", general.log_level)

    def test_general_breadcrumb_trace_enabled_requires_bool(self):
        general = Config.General()
        general.breadcrumb_trace_enabled = False

        with self.assertRaises(ConfigError) as error:
            general.breadcrumb_trace_enabled = 1
        self.assertEqual(
            "Bad config: General.breadcrumb_trace_enabled (1) must be a boolean value",
            str(error.exception)
        )

    def test_general_redaction_flag_requires_bool(self):
        general = Config.General()
        general.config_api_redact_remote_details = False

        with self.assertRaises(ConfigError) as error:
            general.config_api_redact_remote_details = "False"
        self.assertEqual(
            "Bad config: General.config_api_redact_remote_details (False) must be a boolean value",
            str(error.exception)
        )

    def test_general_breadcrumb_trace_retention_depth_requires_bounded_positive_int(self):
        general = Config.General()
        general.breadcrumb_trace_retention_depth = 128

        with self.assertRaises(ConfigError) as error:
            general.breadcrumb_trace_retention_depth = 0
        self.assertEqual(
            "Bad config: General.breadcrumb_trace_retention_depth (0) must be greater than 0",
            str(error.exception)
        )

        with self.assertRaises(ConfigError) as error:
            general.breadcrumb_trace_retention_depth = 1025
        self.assertEqual(
            "Bad config: General.breadcrumb_trace_retention_depth (1025) must not exceed 1024 (FD_SETSIZE limit)",
            str(error.exception)
        )

    def test_general_breadcrumb_trace_retention_depth_defaults_to_128(self):
        good_dict = {
            "log_level": "DEBUG",
            "verbose": "False",
            "api_token": "token-value",
            "allowed_hostname": "",
            "trusted_browser_bootstrap_remote_addrs": "172.25.0.1/32",
            "browser_handover_recovery_version": "2026.04.03",
            "breadcrumb_trace_enabled": "False",
            "config_api_redact_remote_details": "False",
        }

        general = Config.General.from_dict(good_dict)
        self.assertEqual(128, general.breadcrumb_trace_retention_depth)

    def test_lftp(self):
        good_dict = {
            "transfer_backend": "lftp",
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "False",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "net_socket_buffer": "512K",
            "staging_path": "/path/on/local/server/incomplete",
            "protocol": "ftps",
            "remote_ftp_port": "2121",
            "ftp_ssl_verify_certificate": "True",
            "remote_python_path": "/opt/python/bin/python3",
        }
        lftp = Config.Lftp.from_dict(good_dict)
        self.assertEqual("remote.server.com", lftp.remote_address)
        self.assertEqual("lftp", lftp.transfer_backend)
        self.assertEqual("remote-user", lftp.remote_username)
        self.assertEqual("password", lftp.remote_password)
        self.assertEqual(3456, lftp.remote_port)
        self.assertEqual("/path/on/remote/server", lftp.remote_path)
        self.assertEqual("/path/on/local/server", lftp.local_path)
        self.assertEqual("/path/on/remote/server/to/scan/script", lftp.remote_path_to_scan_script)
        self.assertEqual("/opt/python/bin/python3", lftp.remote_python_path)
        self.assertEqual(False, lftp.use_ssh_key)
        self.assertEqual(2, lftp.num_max_parallel_downloads)
        self.assertEqual(3, lftp.num_max_parallel_files_per_download)
        self.assertEqual(4, lftp.num_max_connections_per_root_file)
        self.assertEqual(6, lftp.num_max_connections_per_dir_file)
        self.assertEqual(7, lftp.num_max_total_connections)
        self.assertEqual(True, lftp.use_temp_file)
        self.assertEqual("1M", lftp.rate_limit)
        self.assertEqual("512K", lftp.net_socket_buffer)
        self.assertEqual("/path/on/local/server/incomplete", lftp.staging_path)
        self.assertEqual("ftps", lftp.protocol)
        self.assertEqual(2121, lftp.remote_ftp_port)
        self.assertEqual(True, lftp.ftp_ssl_verify_certificate)

        self.check_common(Config.Lftp,
                          good_dict,
                          {
                              "remote_address",
                              "remote_username",
                              "remote_port",
                              "remote_path",
                              "local_path",
                              "remote_path_to_scan_script",
                              "use_ssh_key",
                              "num_max_parallel_downloads",
                              "num_max_parallel_files_per_download",
                              "num_max_connections_per_root_file",
                              "num_max_connections_per_dir_file",
                              "num_max_total_connections",
                              "use_temp_file"
                          })

        # bad values
        self.check_bad_value_error(Config.Lftp, good_dict, "remote_port", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "remote_port", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "use_ssh_key", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "use_ssh_key", "SomeString")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_parallel_downloads", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_parallel_downloads", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_parallel_files_per_download", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_parallel_files_per_download", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_connections_per_root_file", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_connections_per_root_file", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_connections_per_dir_file", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_connections_per_dir_file", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_total_connections", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_total_connections", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "num_max_total_connections", "33")
        self.check_bad_value_error(Config.Lftp, good_dict, "remote_password", "   ")
        for bad_password in ("line\nbreak", "carriage\rreturn", "tab\tvalue", "null\x00value", "delete\x7fvalue", "escape\x1bvalue"):
            with self.subTest(bad_password=repr(bad_password)):
                self.check_bad_value_error(Config.Lftp, good_dict, "remote_password", bad_password)
        self.check_bad_value_error(Config.Lftp, good_dict, "use_temp_file", "-1")
        self.check_bad_value_error(Config.Lftp, good_dict, "use_temp_file", "SomeString")
        self.check_bad_value_error(Config.Lftp, good_dict, "remote_address", "remote.server.com\nhost = injected")
        self.check_bad_value_error(Config.Lftp, good_dict, "remote_username", "remote-user\r\nuser = injected")
        self.check_bad_value_error(Config.Lftp, good_dict, "net_socket_buffer", "512KB")
        self.check_bad_value_error(Config.Lftp, good_dict, "protocol", "ftp")
        self.check_bad_value_error(Config.Lftp, good_dict, "remote_ftp_port", "0")
        self.check_bad_value_error(Config.Lftp, good_dict, "ftp_ssl_verify_certificate", "SomeString")

    def test_lftp_password_allows_ordinary_punctuation_and_unicode(self):
        lftp = Config.Lftp()
        password = 'spaces, punctuation: "quotes" and unicode ü'

        lftp.remote_password = password

        self.assertEqual(password, lftp.remote_password)

    def test_validate(self):
        good_dict = {
            "xfer_verify": "True",
        }
        validate = Config.Validate.from_dict(good_dict)
        self.assertEqual(True, validate.xfer_verify)
        self.__check_unknown_error(Config.Validate, good_dict)
        self.__check_empty_error(Config.Validate, good_dict, "xfer_verify")
        self.check_bad_value_error(Config.Validate, good_dict, "xfer_verify", "SomeString")
        self.check_bad_value_error(Config.Validate, good_dict, "xfer_verify", "-1")

    def test_validate_defaults(self):
        validate = Config.Validate()
        self.assertEqual(True, validate.xfer_verify)
        self.assertEqual(True, Config.Validate.from_dict({}).xfer_verify)

    def test_lftp_backfills_remote_python_path_to_python3_for_blank_values(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "True",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "net_socket_buffer": "512K",
            "staging_path": "/path/on/local/server/incomplete"
        }

        for remote_python_path in ("", "   "):
            with self.subTest(remote_python_path=remote_python_path):
                lftp = Config.Lftp.from_dict(dict(good_dict, remote_python_path=remote_python_path))
                self.assertEqual("python3", lftp.remote_python_path)

    def test_lftp_backfills_transfer_protocol_defaults(self):
        good_dict = {
            "transfer_backend": "lftp",
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "True",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "net_socket_buffer": "512K",
            "staging_path": "/path/on/local/server/incomplete"
        }

        lftp = Config.Lftp.from_dict(good_dict)

        self.assertEqual("sftp", lftp.protocol)
        self.assertEqual(21, lftp.remote_ftp_port)
        self.assertEqual(True, lftp.ftp_ssl_verify_certificate)

    def test_lftp_backfills_transfer_backend_to_lftp_when_missing_or_invalid(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "False",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
        }

        self.assertEqual("lftp", Config.Lftp.from_dict(good_dict).transfer_backend)
        self.assertEqual("lftp", Config.Lftp.from_dict(dict(good_dict, transfer_backend="bad")).transfer_backend)

    def test_rclone_backend_forces_sftp_protocol(self):
        good_dict = {
            "transfer_backend": "rclone",
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "False",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "protocol": "ftps",
        }

        lftp = Config.Lftp.from_dict(good_dict)
        self.assertEqual("rclone", lftp.transfer_backend)
        self.assertEqual("sftp", lftp.protocol)
        self.assertEqual("python3", lftp.remote_python_path)

    def test_rclone_backend_forces_sftp_protocol_on_direct_property_assignment(self):
        lftp = Config.Lftp()
        lftp.transfer_backend = "rclone"
        lftp.protocol = "ftps"

        self.assertEqual("rclone", lftp.transfer_backend)
        self.assertEqual("sftp", lftp.protocol)

    def test_lftp_allows_empty_net_socket_buffer(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "True",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "net_socket_buffer": "",
            "staging_path": "/path/on/local/server/incomplete"
        }

        lftp = Config.Lftp.from_dict(good_dict)

        self.assertEqual("", lftp.net_socket_buffer)

    def test_lftp_net_socket_buffer_accepts_integer_input(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "True",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "net_socket_buffer": 8388608,
            "staging_path": "/path/on/local/server/incomplete"
        }

        lftp = Config.Lftp.from_dict(good_dict)

        self.assertEqual("8388608", lftp.net_socket_buffer)

    def test_lftp_allows_empty_remote_password(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "True",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "staging_path": "/path/on/local/server/incomplete"
        }

        lftp = Config.Lftp.from_dict(good_dict)

        self.assertEqual("", lftp.remote_password)
        self.assertEqual(True, lftp.use_ssh_key)

        self.check_common(Config.Lftp,
                          good_dict,
                          {
                              "remote_address",
                              "remote_username",
                              "remote_port",
                              "remote_path",
                              "local_path",
                              "remote_path_to_scan_script",
                              "use_ssh_key",
                              "num_max_parallel_downloads",
                              "num_max_parallel_files_per_download",
                              "num_max_connections_per_root_file",
                              "num_max_connections_per_dir_file",
                              "num_max_total_connections",
                              "use_temp_file"
                          })

    def test_lftp_backfills_missing_staging_path(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "use_ssh_key": "False",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M"
        }

        lftp = Config.Lftp.from_dict(good_dict)

        self.assertEqual("", lftp.staging_path)

    def test_lftp_backfills_missing_net_socket_buffer(self):
        good_dict = {
            "remote_address": "remote.server.com",
            "remote_username": "remote-user",
            "remote_password": "password",
            "remote_port": "3456",
            "remote_path": "/path/on/remote/server",
            "local_path": "/path/on/local/server",
            "remote_path_to_scan_script": "/path/on/remote/server/to/scan/script",
            "remote_python_path": "/opt/python/bin/python3",
            "use_ssh_key": "False",
            "num_max_parallel_downloads": "2",
            "num_max_parallel_files_per_download": "3",
            "num_max_connections_per_root_file": "4",
            "num_max_connections_per_dir_file": "6",
            "num_max_total_connections": "7",
            "use_temp_file": "True",
            "rate_limit": "1M",
            "staging_path": "/path/on/local/server/incomplete"
        }

        lftp = Config.Lftp.from_dict(good_dict)

        self.assertEqual("8M", lftp.net_socket_buffer)
        self.assertEqual("sftp", lftp.protocol)
        self.assertEqual(21, lftp.remote_ftp_port)
        self.assertEqual(True, lftp.ftp_ssl_verify_certificate)

    def test_controller(self):
        good_dict = {
            "interval_ms_remote_scan": "30000",
            "interval_ms_local_scan": "10000",
            "interval_ms_downloading_scan": "2000",
            "extract_path": "/extract/path",
            "use_local_path_as_extract_path": "True",
            "managed_extract_folders_enabled": "True"
        }
        controller = Config.Controller.from_dict(good_dict)
        self.assertEqual(30000, controller.interval_ms_remote_scan)
        self.assertEqual(10000, controller.interval_ms_local_scan)
        self.assertEqual(2000, controller.interval_ms_downloading_scan)
        self.assertEqual("/extract/path", controller.extract_path)
        self.assertEqual(True, controller.use_local_path_as_extract_path)
        self.assertEqual(True, controller.managed_extract_folders_enabled)

        controller_default = Config.Controller.from_dict({
            "interval_ms_remote_scan": "30000",
            "interval_ms_local_scan": "10000",
            "interval_ms_downloading_scan": "2000",
            "extract_path": "/extract/path",
            "use_local_path_as_extract_path": "True"
        })
        self.assertEqual(True, controller_default.managed_extract_folders_enabled)

        self.check_common(Config.Controller,
                          good_dict,
                          {
                              "interval_ms_remote_scan",
                              "interval_ms_local_scan",
                              "interval_ms_downloading_scan",
                              "extract_path",
                              "use_local_path_as_extract_path"
                          })

        # bad values
        self.check_bad_value_error(Config.Controller, good_dict, "interval_ms_remote_scan", "-1")
        self.check_bad_value_error(Config.Controller, good_dict, "interval_ms_remote_scan", "0")
        self.check_bad_value_error(Config.Controller, good_dict, "interval_ms_local_scan", "-1")
        self.check_bad_value_error(Config.Controller, good_dict, "interval_ms_local_scan", "0")
        self.check_bad_value_error(Config.Controller, good_dict, "interval_ms_downloading_scan", "-1")
        self.check_bad_value_error(Config.Controller, good_dict, "interval_ms_downloading_scan", "0")
        self.check_bad_value_error(Config.Controller, good_dict, "use_local_path_as_extract_path", "SomeString")
        self.check_bad_value_error(Config.Controller, good_dict, "use_local_path_as_extract_path", "-1")
        self.check_bad_value_error(Config.Controller, good_dict, "managed_extract_folders_enabled", "SomeString")
        self.check_bad_value_error(Config.Controller, good_dict, "managed_extract_folders_enabled", "-1")

    def test_web(self):
        good_dict = {
            "port": "1234",
        }
        web = Config.Web.from_dict(good_dict)
        self.assertEqual(1234, web.port)

        self.check_common(Config.Web,
                          good_dict,
                          {
                              "port"
                          })

        # bad values
        self.check_bad_value_error(Config.Web, good_dict, "port", "-1")
        self.check_bad_value_error(Config.Web, good_dict, "port", "0")

    def test_autoqueue(self):
        good_dict = {
            "enabled": "True",
            "patterns_only": "False",
            "auto_extract": "True",
            "auto_delete_remote": "True"
        }
        autoqueue = Config.AutoQueue.from_dict(good_dict)
        self.assertEqual(True, autoqueue.enabled)
        self.assertEqual(False, autoqueue.patterns_only)
        self.assertEqual(True, autoqueue.auto_delete_remote)

        self.check_common(Config.AutoQueue,
                          good_dict,
                          {
                              "enabled",
                              "patterns_only",
                              "auto_extract"
                          })

        # bad values
        self.check_bad_value_error(Config.AutoQueue, good_dict, "enabled", "SomeString")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "enabled", "-1")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "patterns_only", "SomeString")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "patterns_only", "-1")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "auto_extract", "SomeString")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "auto_extract", "-1")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "auto_delete_remote", "SomeString")
        self.check_bad_value_error(Config.AutoQueue, good_dict, "auto_delete_remote", "-1")

    def test_logging(self):
        self.assertEqual("json", Config.Logging.from_dict({"log_format": "json"}).log_format)
        self.assertEqual("json", Config.Logging.from_dict({"log_format": "JSON"}).log_format)
        self.assertEqual("standard", Config.Logging.from_dict({}).log_format)
        self.assertEqual("standard", Config.Logging.from_dict({"log_format": ""}).log_format)

        with self.assertRaises(ConfigError) as error:
            Config.Logging.from_dict({"log_format": "text"})
        self.assertEqual(
            "Bad config: Logging.log_format (text) must be either standard or json",
            str(error.exception)
        )

    def test_from_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as config_file:
            config_file.write("""
        [General]
        debug=True
        verbose=True
        trusted_browser_bootstrap_remote_addrs=172.25.0.1/32
        browser_handover_recovery_version=2026.04.03

        [Lftp]
        remote_address=remote.server.com
        remote_username=remote-user
        remote_password=remote-pass%value
        remote_port = 3456
        remote_path=/path/on/remote/server
        local_path=/path/on/local/server
        remote_path_to_scan_script=/path/on/remote/server/to/scan/script
        use_ssh_key=True
        num_max_parallel_downloads=2
        num_max_parallel_files_per_download=3
        num_max_connections_per_root_file=4
        num_max_connections_per_dir_file=5
        num_max_total_connections=7
        use_temp_file=False
        rate_limit=500K
        staging_path=/path/on/local/server/incomplete

        [Validate]
        xfer_verify=True

        [Controller]
        interval_ms_remote_scan=30000
        interval_ms_local_scan=10000
        interval_ms_downloading_scan=2000
        extract_path=/path/where/to/extract/stuff
        use_local_path_as_extract_path=False
        managed_extract_folders_enabled=True

        [Web]
        port=88

        [AutoQueue]
        enabled=False
        patterns_only=True
        auto_extract=True

        [Logging]
        log_format=
        """)
            config_path = config_file.name

        try:
            config = Config.from_file(config_path)

            self.assertEqual("DEBUG", config.general.log_level)
            self.assertEqual(True, config.general.verbose)
            self.assertEqual("", config.general.api_token)
            self.assertEqual("", config.general.exclude_patterns)
            self.assertEqual("", config.general.allowed_hostname)
            self.assertFalse(config.general.has_property("trusted_browser_bootstrap_remote_addrs"))
            self.assertEqual("2026.04.03", config.general.browser_handover_recovery_version)
            self.assertEqual(False, config.general.breadcrumb_trace_enabled)
            self.assertEqual(128, config.general.breadcrumb_trace_retention_depth)
            self.assertEqual(True, config.general.config_api_redact_remote_details)

            self.assertEqual("remote.server.com", config.lftp.remote_address)
            self.assertEqual("remote-user", config.lftp.remote_username)
            self.assertEqual("remote-pass%value", config.lftp.remote_password)
            self.assertEqual(3456, config.lftp.remote_port)
            self.assertEqual("/path/on/remote/server", config.lftp.remote_path)
            self.assertEqual("/path/on/local/server", config.lftp.local_path)
            self.assertEqual("/path/on/remote/server/to/scan/script", config.lftp.remote_path_to_scan_script)
            self.assertEqual("python3", config.lftp.remote_python_path)
            self.assertEqual(True, config.lftp.use_ssh_key)
            self.assertEqual(2, config.lftp.num_max_parallel_downloads)
            self.assertEqual(3, config.lftp.num_max_parallel_files_per_download)
            self.assertEqual(4, config.lftp.num_max_connections_per_root_file)
            self.assertEqual(5, config.lftp.num_max_connections_per_dir_file)
            self.assertEqual(7, config.lftp.num_max_total_connections)
            self.assertEqual(False, config.lftp.use_temp_file)
            self.assertEqual("500K", config.lftp.rate_limit)
            self.assertEqual("/path/on/local/server/incomplete", config.lftp.staging_path)
            self.assertEqual("sftp", config.lftp.protocol)
            self.assertEqual(21, config.lftp.remote_ftp_port)
            self.assertEqual(True, config.lftp.ftp_ssl_verify_certificate)
            self.assertEqual(True, config.validate.xfer_verify)

            self.assertEqual(30000, config.controller.interval_ms_remote_scan)
            self.assertEqual(10000, config.controller.interval_ms_local_scan)
            self.assertEqual(2000, config.controller.interval_ms_downloading_scan)
            self.assertEqual("/path/where/to/extract/stuff", config.controller.extract_path)
            self.assertEqual(False, config.controller.use_local_path_as_extract_path)
            self.assertEqual(True, config.controller.managed_extract_folders_enabled)

            self.assertEqual(88, config.web.port)

            self.assertEqual(False, config.autoqueue.enabled)
            self.assertEqual(True, config.autoqueue.patterns_only)
            self.assertEqual(True, config.autoqueue.auto_extract)
            self.assertEqual(False, config.autoqueue.auto_delete_remote)
            self.assertEqual("standard", config.logging.log_format)

            with tempfile.NamedTemporaryFile("w", delete=False) as saved_config_file:
                saved_config_path = saved_config_file.name
            try:
                config.to_file(saved_config_path)
                with open(saved_config_path, "r", encoding="utf-8") as saved_config_file:
                    self.assertNotIn(
                        "trusted_browser_bootstrap_remote_addrs",
                        saved_config_file.read(),
                    )
            finally:
                os.remove(saved_config_path)

            # unknown section error
            with open(config_path, "a") as config_file:
                config_file.write("""
        [Unknown]
        key=value
                """)
            with self.assertRaises(ConfigError) as error:
                Config.from_file(config_path)
            self.assertTrue(str(error.exception).startswith("Unknown section"))
        finally:
            os.remove(config_path)

    def test_to_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as config_file:
            config_file_path = config_file.name

        try:
            config = Config()
            config.general.log_level = "DEBUG"
            config.general.verbose = False
            config.general.exclude_patterns = "*.nfo,Sample/"
            config.general.api_token = "api-token-value"
            config.general.allowed_hostname = ""
            config.general.browser_handover_recovery_version = "2026.04.03"
            config.general.breadcrumb_trace_enabled = False
            config.general.breadcrumb_trace_retention_depth = 128
            config.general.config_api_redact_remote_details = True
            config.lftp.remote_address = "server.remote.com"
            config.lftp.remote_username = "user-on-remote-server"
            config.lftp.remote_password = "pass-on-remote%server"
            config.lftp.remote_port = 3456
            config.lftp.remote_path = "/remote/server/path"
            config.lftp.local_path = "/local/server/path"
            config.lftp.remote_path_to_scan_script = "/remote/server/path/to/script"
            config.lftp.remote_python_path = "python3"
            config.lftp.use_ssh_key = True
            config.lftp.num_max_parallel_downloads = 6
            config.lftp.num_max_parallel_files_per_download = 7
            config.lftp.num_max_connections_per_root_file = 2
            config.lftp.num_max_connections_per_dir_file = 3
            config.lftp.num_max_total_connections = 4
            config.lftp.use_temp_file = True
            config.lftp.staging_path = "/local/server/path/incomplete"
            config.lftp.protocol = "sftp"
            config.lftp.remote_ftp_port = 21
            config.lftp.ftp_ssl_verify_certificate = True
            config.validate.xfer_verify = True
            config.controller.interval_ms_remote_scan = 1234
            config.controller.interval_ms_local_scan = 5678
            config.controller.interval_ms_downloading_scan = 9012
            config.controller.extract_path = "/path/extract/stuff"
            config.controller.use_local_path_as_extract_path = True
            config.controller.managed_extract_folders_enabled = False
            config.web.port = 13
            config.autoqueue.enabled = True
            config.autoqueue.patterns_only = True
            config.autoqueue.auto_extract = False
            config.autoqueue.auto_delete_remote = False
            config.logging.log_format = "JSON"
            config.to_file(config_file_path)
            with open(config_file_path, "r") as f:
                actual_str = f.read()

            golden_str = """
            [General]
            log_level = DEBUG
            verbose = False
            exclude_patterns = *.nfo,Sample/
            api_token = api-token-value
            allowed_hostname =
            browser_handover_recovery_version = 2026.04.03
            disable_browser_auth = False
            breadcrumb_trace_enabled = False
            breadcrumb_trace_retention_depth = 128
            config_api_redact_remote_details = True

            [Lftp]
            transfer_backend = lftp
            remote_address = server.remote.com
            remote_username = user-on-remote-server
            remote_password = pass-on-remote%server
            remote_port = 3456
            remote_path = /remote/server/path
            local_path = /local/server/path
            remote_path_to_scan_script = /remote/server/path/to/script
            remote_python_path = python3
            use_ssh_key = True
            num_max_parallel_downloads = 6
            num_max_parallel_files_per_download = 7
            num_max_connections_per_root_file = 2
            num_max_connections_per_dir_file = 3
            num_max_total_connections = 4
            use_temp_file = True
            rate_limit = None
            net_socket_buffer = None
            staging_path = /local/server/path/incomplete
            protocol = sftp
            remote_ftp_port = 21
            ftp_ssl_verify_certificate = True
            use_legacy_lftp_password_argv = False

            [Validate]
            xfer_verify = True

            [Controller]
            interval_ms_remote_scan = 1234
            interval_ms_local_scan = 5678
            interval_ms_downloading_scan = 9012
            extract_path = /path/extract/stuff
            use_local_path_as_extract_path = True
            managed_extract_folders_enabled = False

            [Web]
            port = 13

            [AutoQueue]
            enabled = True
            patterns_only = True
            auto_extract = False
            auto_delete_remote = False

            [Logging]
            log_format = json

            [Notifications]
            enabled = False
            provider = webhook
            webhook_url =
            hmac_secret =
            apprise_url =
            apprise_tag =
            allow_private_networks = False
            download_start = False
            download_complete = True
            extraction_complete = True
            delete_complete = True
            """

            golden_lines = [s.strip() for s in golden_str.splitlines()]
            golden_lines = list(filter(None, golden_lines))  # remove blank lines
            actual_lines = [s.strip() for s in actual_str.splitlines()]
            actual_lines = list(filter(None, actual_lines))  # remove blank lines

            self.assertEqual(len(golden_lines), len(actual_lines))
            for i, _ in enumerate(golden_lines):
                self.assertEqual(golden_lines[i], actual_lines[i])
        finally:
            os.remove(config_file_path)

    def test_to_file_requires_bound_path(self):
        config = Config()

        with self.assertRaises(PersistError) as error:
            config.to_file()

        self.assertEqual("Config file path is not bound", str(error.exception))

    def test_persist_read_error(self):
        # bad section
        content = """
        [Web
        port=88
        """
        with self.assertRaises(PersistError):
            Config.from_str(content)

        # bad value
        content = """
        [Web]
        port88
        """
        with self.assertRaises(PersistError):
            Config.from_str(content)

        # bad line
        content = """
        [Web]
        port=88
        what am i doing here
        """
        with self.assertRaises(PersistError):
            Config.from_str(content)
