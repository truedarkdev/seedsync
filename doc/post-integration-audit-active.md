# Post-Integration Audit Active Ledger

This file contains only unfinished audit rows.

Open this file by default during active audit work. Completed rows move into per-fork archive files in `doc/post-integration-audit-archive/` in chunks of up to 50 finished commits.

Related files:
- Rules and workflow: [post-integration-audit-rules.md](/mnt/c/Git/seedsync/doc/post-integration-audit-rules.md)
- Audit landing page and archive index: [post-integration-audit.md](/mnt/c/Git/seedsync/doc/post-integration-audit.md)

## thejuran

Audit base: `origin/master @ ff2a1039935beccbbf7ec76134b41d2e91137742`
Source branch: `thejuran/master`
Fork tip at audit start: `a8561cdc318460de32de082e3cf33f6b6a0093cb`
Inventory status: `complete`
Audit state: `in progress`
Pass date: `2026-03-11`
Maintainer-approved batch size: `27`

Open rows in this file: `293 / 672 remaining`

| Commit | Upstream commit subject | Mapped integration subject | Triage outcome | Confidence | Evidence | Reviewer needed | Coverage | Final disposition | Follow-up / proof |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `03869bcec77c791d7ca77276f0654b9d226ad3c1` | docs(18): complete Phase 18 state updates and summaries | unknown | unprocessed | — | — | — | — | — | — |
| `1a71ac086c623b65efa0bdb3614bc9b9d9707c03` | chore: archive v1.5 Backend Testing milestone | unknown | unprocessed | — | — | — | — | — | — |
| `5c3526f706c6b76aec2ee14c60b194b3a0566d0d` | feat: add Remote Added and Local Added timestamp columns to file list | unknown | unprocessed | — | — | — | — | — | — |
| `569622eb3b39d6ffe3a9e27cbc5dc5143fe8c96a` | fix: fall back to st_ctime when st_birthtime unavailable on Linux | unknown | unprocessed | — | — | — | — | — | — |
| `ab39d7f922af42630c4c0b4bc846bb652aac5aba` | chore: bump version to v1.3.0 | unknown | unprocessed | — | — | — | — | — | — |
| `a9ce3dc19f56336df52187ab0a1da89f8b43856b` | docs: add v1.3.0 changelog and update install pinned version | unknown | unprocessed | — | — | — | — | — | — |
| `7d85e6aed9233e07d92c255f075e7533007a5686` | fix: patch created timestamps in integration tests for Linux st_ctime fallback | unknown | unprocessed | — | — | — | — | — | — |
| `0fff1466b73e1289590b409df993f58fa4368b40` | docs: capture todo - clean up CI test runner warnings | unknown | unprocessed | — | — | — | — | — | — |
| `a0b0e5fb2b70db373a7107d0c56303ef839bb838` | fix: add checkout step to publish-deb job and handle existing releases | unknown | unprocessed | — | — | — | — | — | — |
| `55a45c440b6d8316eb864844e45ac2e0746c8b2d` | docs: start milestone v1.6 CI Cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `56e2bfe068106ba566e3617ed7ea886d2c03cbae` | docs: define milestone v1.6 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `ce9259ecf3831b38987f57524cd790b125ee4846` | docs: create milestone v1.6 roadmap (2 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `c4d8b4fc5d526e0a48dacff906be5f7a1ddcb1a8` | docs(20): create phase plan for CI workflow consolidation | unknown | unprocessed | — | — | — | — | — | — |
| `eab61461f5cfff854bc5637979a6f982be192044` | feat(20-01): add :dev Docker publishing job to master.yml | unknown | unprocessed | — | — | — | — | — | — |
| `268a86b72d78994acf0e801c3e9099e3158094ec` | feat(20-01): delete docker-publish.yml and update CLAUDE.md | unknown | unprocessed | — | — | — | — | — | — |
| `2ffe1588d309ec1d837c1bef1ed9c01524a56ff3` | docs(20-01): complete CI workflow consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `fb4dcacf35ed9c9080f1c5c8bbb95266cce61c49` | docs(phase-20): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `9eafceabebe11f054725fec53b6046d91ec8cf39` | feat(21-01): suppress pytest cache and cgi deprecation warnings | unknown | unprocessed | — | — | — | — | — | — |
| `c3d369f1e1f4a7b6b15f52959a87a7dcb5da24f7` | docs(phase-21): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `2c6d72293a78ce6daf8f8490ed09baa5ac17da6f` | chore: archive v1.6 CI Cleanup milestone | unknown | unprocessed | — | — | — | — | — | — |
| `bc35c346974a2a41600d2116bcfe4da12f9391c9` | docs: start milestone v1.7 Sonarr Integration | unknown | unprocessed | — | — | — | — | — | — |
| `6c7b38423e5bdba1cdb1e56ed866beeaa94caacd` | docs: complete v1.7 Sonarr integration research | unknown | unprocessed | — | — | — | — | — | — |
| `9cc22591ec6b080ef97d14b9f73f89bde69b9bb2` | docs: define milestone v1.7 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `261c640f2c567145ab069a7dce1faeb929ee49fa` | docs: create milestone v1.7 roadmap (4 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `dc44ab93f59259909bfd5b45a0d76ee0e3a22857` | docs(22): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `724270465656e62998fae3ece2d9f31f0af86aa1` | feat(22-01): add Config.Sonarr InnerConfig class | unknown | unprocessed | — | — | — | — | — | — |
| `312f4604c1f6d927f28a4751396c528ab25d17e4` | feat(22-01): add Sonarr defaults to _create_default_config | unknown | unprocessed | — | — | — | — | — | — |
| `850f5006b3063a75d7485831ee7d2d6f096f9a4a` | feat(22-01): add Sonarr test connection endpoint | unknown | unprocessed | — | — | — | — | — | — |
| `e14ed8c1ab9890bdf9cd6c9eb5cec997f0d6713d` | docs(22-01): complete Backend Config + Test Connection plan | unknown | unprocessed | — | — | — | — | — | — |
| `587e8de2febf1f5a139027401f9174715893be48` | feat(22-02): add ISonarr interface and SonarrRecord to config model | unknown | unprocessed | — | — | — | — | — | — |
| `0b53f93f5b968eb05d6bf69fcd7fd714e893b263` | feat(22-02): add testSonarrConnection method to ConfigService | unknown | unprocessed | — | — | — | — | — | — |
| `2b89bbcc4659d77f2a2157c38b68e22b1aaab78f` | feat(22-02): add *arr Integration section to settings template | unknown | unprocessed | — | — | — | — | — | — |
| `b5c92463c9fd8878090db32661ab1c18ba30140d` | feat(22-02): add test connection logic and state to settings component | unknown | unprocessed | — | — | — | — | — | — |
| `5f857957c79b2ac3604f70f6f44dc3b18db197c2` | feat(22-02): add test connection styles | unknown | unprocessed | — | — | — | — | — | — |
| `6c91ec334fbb053e92b19b268a46dd2153d38eea` | fix(22-02): remove inferrable type annotation to pass lint | unknown | unprocessed | — | — | — | — | — | — |
| `d8ba19b82a5ccdd56f67d10d4c4f8e9e04f230ab` | docs(22-02): complete Frontend Settings UI plan | unknown | unprocessed | — | — | — | — | — | — |
| `159206a1486495ffa5d93887785f76052150d957` | docs: update state for Phase 22 completion | unknown | unprocessed | — | — | — | — | — | — |
| `6eb2eb2fe51c82472d2b5e651451ccbb116541d6` | docs(23): create phase plan for API Client Integration | unknown | unprocessed | — | — | — | — | — | — |
| `c9f5aae8aaec559413d5c7085a34dcbddead59ec` | docs(23): fix memory monitor assertion count in test plan | unknown | unprocessed | — | — | — | — | — | — |
| `6420549983176eddbabe4af3922774a26da9306e` | feat(23-01): add SonarrManager, imported_file_names persist, Controller integration | unknown | unprocessed | — | — | — | — | — | — |
| `aec281c1460749b3f24943979fd869be460a5e38` | test(23-02): add SonarrManager tests, update persist and controller tests | unknown | unprocessed | — | — | — | — | — | — |
| `e7ebb963be85ab0e1da26971329099693903a8bf` | docs: update state for Phase 23 completion | unknown | unprocessed | — | — | — | — | — | — |
| `a613da88a21632f245aed9fddbdb10122558a423` | feat(24-01): add ImportStatus enum and import_status property to ModelFile | unknown | unprocessed | — | — | — | — | — | — |
| `9444eb209a8a386b8ca427532044bff2dff14551` | feat(24-01): serialize import_status in SerializeModel | unknown | unprocessed | — | — | — | — | — | — |
| `5b52854bf837b1618ab6d6726cb7c7e2de8d9330` | feat(24-01): set import_status on model files in Controller | unknown | unprocessed | — | — | — | — | — | — |
| `9a3301a13d2933d0666407184fdf3e673b4605e4` | feat(24-01): add import_status to frontend ModelFile | unknown | unprocessed | — | — | — | — | — | — |
| `4f2d84e8c69ae0a2f053baaf57709c453516e7ed` | feat(24-01): add importStatus to ViewFile and ViewFileService mapping | unknown | unprocessed | — | — | — | — | — | — |
| `3a0031add83793a2f57ba55be1877158a87f59ad` | feat(24-01): create ToastService for ephemeral notifications | unknown | unprocessed | — | — | — | — | — | — |
| `6bf18e7cc02604c3c16c52460be734e25958c182` | feat(24-01): integrate toast container into app component | unknown | unprocessed | — | — | — | — | — | — |
| `f0bde8e6bfb2cfa4ec2045b6471107ec3690bde4` | docs(24-01): complete import_status pipeline and toast service plan | unknown | unprocessed | — | — | — | — | — | — |
| `b98b68bedcb470eb31b156f8774e19fbdb388a56` | feat(24-02): add import status badge to file component template | unknown | unprocessed | — | — | — | — | — | — |
| `50cb979af86bd0d9b7178b0f7e9686d7bcc70986` | feat(24-02): add import badge styling to file component SCSS | unknown | unprocessed | — | — | — | — | — | — |
| `13d8e961d14b64563ba8c58c750fcaeedc6717c4` | feat(24-02): add import detection and toast triggering in file-list component | unknown | unprocessed | — | — | — | — | — | — |
| `8f94beb187e57b4cedbfa32d846d0ee8c1eeb3a8` | docs(24-02): complete import badge and toast triggering plan | unknown | unprocessed | — | — | — | — | — | — |
| `428bd189048287885add5276758183cdc4cfef84` | fix(24): add import_status to expected ModelFile objects in model-file.service.spec.ts | unknown | unprocessed | — | — | — | — | — | — |
| `64d6447d17ad6206e644094b421d197317da397e` | docs(25): create phase plan for auto-delete with safety | unknown | unprocessed | — | — | — | — | — | — |
| `24a9698240691b45b7ae8d1c1d5f1a7b68c10fde` | feat(25-01): add Config.AutoDelete InnerConfig section and default values | unknown | unprocessed | — | — | — | — | — | — |
| `9929c512be75fc05b1b1aa3793f27941cbf22889` | feat(25-01): add frontend AutoDelete config model and settings UI section | unknown | unprocessed | — | — | — | — | — | — |
| `9f399fe0114b0be3fa89eac9ac6e9d88c43b783d` | docs(25-01): complete Config.AutoDelete section plan | unknown | unprocessed | — | — | — | — | — | — |
| `c8f01c54dd9dbf655d5ff50f702b50cfe1884a3a` | feat(25-02): add auto-delete scheduling and execution to Controller | unknown | unprocessed | — | — | — | — | — | — |
| `a4faeef7c260eb20a088fa80c6b6a727dfc055b3` | test(25-02): add unit tests for auto-delete scheduling, execution, and safety | unknown | unprocessed | — | — | — | — | — | — |
| `af07454290586b77c70b20a7e405f6334af7a84d` | docs(25-02): complete auto-delete timer logic plan | unknown | unprocessed | — | — | — | — | — | — |
| `02edd2378cdeb9baf3dcd4da1df1311c27c4f923` | fix(tests): add AutoDelete section to test_to_file golden string and test_has_section | unknown | unprocessed | — | — | — | — | — | — |
| `e053b96ee900ab9bad9bd7432399866ee0ba618a` | fix(config): default Sonarr and AutoDelete values for existing installs | unknown | unprocessed | — | — | — | — | — | — |
| `e6460717d47d36fe29a8e20e070b43e19efaf7e2` | test(22): complete UAT - 6 passed, 0 issues | unknown | unprocessed | — | — | — | — | — | — |
| `7f8b4b892cd6212bd4113bbacd21b7b00881e462` | test(23): complete UAT - 3 passed, 0 issues | unknown | unprocessed | — | — | — | — | — | — |
| `b356607e795f3e48264f284dc165d597c37fa58c` | fix(toast): prevent repeated import toast notifications on every poll cycle | unknown | unprocessed | — | — | — | — | — | — |
| `57b815a8b748d13d328d4af440023198542d4c5b` | chore: archive v1.7 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `c5b71a9d676badab6b8064f2c79de1e0d8470b0f` | docs: start milestone v1.8 Radarr + Webhooks | unknown | unprocessed | — | — | — | — | — | — |
| `c72277e91a5893eb031b28dfa4a8dbbc8c396781` | docs: update v1.8 research with detailed webhook payloads | unknown | unprocessed | — | — | — | — | — | — |
| `1fbb08da7bbcef485c348fe2b71a2f11c2c5a4c3` | docs(26): create phase plans for Radarr config and shared *arr settings UI | unknown | unprocessed | — | — | — | — | — | — |
| `d12305e7da5ea3f4c8cab5bc9a7098f8b517838f` | feat(26-01): add Config.Radarr InnerConfig class with backward-compatible parsing | unknown | unprocessed | — | — | — | — | — | — |
| `815a19ded49e1fe2b151b5695c70f487e26b499a` | feat(26-01): add Radarr test connection endpoint and unit tests | unknown | unprocessed | — | — | — | — | — | — |
| `86126ce3d79a272faea8b36c78854f93eeccc0e0` | docs(26-01): complete Radarr backend config plan summary and update state | unknown | unprocessed | — | — | — | — | — | — |
| `d68fcf7423b76086e6ef5abb402058fbad05d52d` | feat(26-02): add IRadarr frontend model and testRadarrConnection service method | unknown | unprocessed | — | — | — | — | — | — |
| `7bebe91132a9e985fad0bd7fc8df556f766760c4` | feat(26-02): refactor *arr Integration UI with Sonarr and Radarr subsections | unknown | unprocessed | — | — | — | — | — | — |
| `29da0430084814ef3f238d2f8182a4405b8901a3` | docs(26-02): complete Radarr frontend config and shared *arr UI plan summary | unknown | unprocessed | — | — | — | — | — | — |
| `f480f8ef7140665ca1486d6572fb9772fe137db6` | docs(phase-27): research webhook import detection domain | unknown | unprocessed | — | — | — | — | — | — |
| `4c773c39edf2c58a0a5f85749b9de3f796b5af7a` | docs(27): create phase plans for webhook import detection | unknown | unprocessed | — | — | — | — | — | — |
| `84a365a79bca8a784de5738ab480e3b756336fa7` | feat(27-02): add webhook URL display to Settings page | unknown | unprocessed | — | — | — | — | — | — |
| `e3b554a88d48eddb67710ac6ee756df79f11e4f8` | docs(27-02): complete webhook URL display plan | unknown | unprocessed | — | — | — | — | — | — |
| `cd8d78ad40cd1bd47f7ed414e84a90bb8135e70f` | feat(27-01): replace SonarrManager with WebhookManager | unknown | unprocessed | — | — | — | — | — | — |
| `87d1aa77852962e0587e964b68ad1001f208a937` | test(27-01): update tests for WebhookManager migration | unknown | unprocessed | — | — | — | — | — | — |
| `b976c75b53418bfa270e5f01863af048b5bdeb93` | docs(27-01): complete webhook backend plan | unknown | unprocessed | — | — | — | — | — | — |
| `5e6204249cfc71a538ea674215be53a1864fb9eb` | docs(phase-27): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `e778d96b4b5d8a87a71b9fccc7fd4ba36ee31d6e` | docs(28): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `9a2d5dbf1024cfc78e22934ca23f6b8b050e6a1d` | docs(28-01): phase 28 verification summary - all Angular tests passing | unknown | unprocessed | — | — | — | — | — | — |
| `6c1d514ece67608ea215a31e35aef1f35d66741c` | docs: mark phase 28 and v1.8 milestone complete | unknown | unprocessed | — | — | — | — | — | — |
| `0d5b8502e7b2d08fd7d950f4497589d30f67b282` | chore: archive v1.8 milestone — Radarr + Webhooks shipped | unknown | unprocessed | — | — | — | — | — | — |
| `b981c7b26301140871380372fff21f74220ed7c0` | docs: start milestone v2.0 Dark Mode & Polish | unknown | unprocessed | — | — | — | — | — | — |
| `b9966c020e02a80b126920cbeebfb7382b13300e` | docs: complete v2.0 domain research | unknown | unprocessed | — | — | — | — | — | — |
| `870b4d10a90e2cf1d6305001ecf5936bd280d131` | docs: define milestone v2.0 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `1d88ba4fa2ea21ec8918b3bf410fbce8874fdd6c` | docs: create milestone v2.0 roadmap (4 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `fb256ba142f93d9109163acec7f764b4468c1b95` | docs(29): create phase plan for theme infrastructure | unknown | unprocessed | — | — | — | — | — | — |
| `ad35d2e5096542c8a49954797479a2cdbe9b02f3` | feat(29-01): create ThemeService with signal-based state management | unknown | unprocessed | — | — | — | — | — | — |
| `7e4157ba9a6eeb376b8147b82619200c5b3220c1` | feat(29-01): add FOUC prevention and register ThemeService | unknown | unprocessed | — | — | — | — | — | — |
| `9423a971071ff657ad7745d8c66681c3ffb25669` | docs(29-01): complete theme infrastructure plan | unknown | unprocessed | — | — | — | — | — | — |
| `ce373a2b3132bfe6d8345a5180da5539910e004b` | test(29-02): add comprehensive ThemeService unit tests | unknown | unprocessed | — | — | — | — | — | — |
| `691d94526989859c78c03d8e98195804171d7315` | docs(29-02): complete ThemeService testing plan | unknown | unprocessed | — | — | — | — | — | — |
| `6d2e19681f383599c2d8c53cbee54532711a1e99` | docs(phase-29): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `281be15dcaf6d8de05936d090bf91fc0725f1347` | docs(30): research phase domain | unknown | unprocessed | — | — | — | — | — | — |
| `76300c10c990704ddcea40e1617f52c9b3dfe421` | docs(30): create phase plan for SCSS audit and color fixes | unknown | unprocessed | — | — | — | — | — | — |
| `9a32aa9f5b52d21ab643a6bacb47b7d95d0bb47f` | feat(30-01): add custom CSS variables and theme-aware form controls | unknown | unprocessed | — | — | — | — | — | — |
| `88d6258e823df33a6a8826453573b1db065997c6` | feat(30-01): remove hardcoded data-bs-theme from dropdowns | unknown | unprocessed | — | — | — | — | — | — |
| `92d4c2ad8d3ecc958ef6745714150d7dd84bb7a7` | docs(30-01): complete custom CSS variables and theme-aware forms plan | unknown | unprocessed | — | — | — | — | — | — |
| `3f1d7d2ef611f7a0e10e2b4e43f039bbbf0e2d84` | feat(30-02): migrate app, file-list, and logs component SCSS to theme-aware CSS variables | unknown | unprocessed | — | — | — | — | — | — |
| `27fb582e902fb10e5c374545d5e048e206bfe15f` | feat(30-02): migrate about, file, sidebar, and header component SCSS to theme-aware CSS variables | unknown | unprocessed | — | — | — | — | — | — |
| `0eeef6cfae686625b4c1a148caa985d41680d497` | docs(30-02): complete Component SCSS Migrations plan | unknown | unprocessed | — | — | — | — | — | — |
| `e5989061518771a04fbf84723027840e3bc602de` | docs(phase-30): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `fda674ebf60de396385ec53ddbb2e3086f564aa9` | docs(31): research theme toggle UI patterns | unknown | unprocessed | — | — | — | — | — | — |
| `04d822e8d15aa60cb41bf2842830dff33228b61b` | docs(31): create phase plan for theme toggle UI | unknown | unprocessed | — | — | — | — | — | — |
| `528e845b009eb2becf78630e71e839a12d7e5d37` | feat(31-01): add Appearance section with theme toggle to Settings page | unknown | unprocessed | — | — | — | — | — | — |
| `facb52b4e10fa652630695fc17335625f6258d40` | test(31-01): add unit tests for Settings page theme toggle | unknown | unprocessed | — | — | — | — | — | — |
| `64088736c96d4a01e5e2404ec5dafd058c32b226` | docs(31-01): complete Theme Toggle UI plan | unknown | unprocessed | — | — | — | — | — | — |
| `f88f2372f9eff5c1aca5afe501c04e951dc1a80b` | docs(phase-31): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `5808c129f5b78be98470a128ecd5dbaafd3bc6f6` | docs(32): research cosmetic fixes phase | unknown | unprocessed | — | — | — | — | — | — |
| `c6ab63e2d77826d85b34bc5ab35db31a32aaa125` | docs(32): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `123580b03c578fcd0b4efa3ed7da36a129de5f54` | feat(32-01): update *arr text references to Sonarr/Radarr | unknown | unprocessed | — | — | — | — | — | — |
| `2e54493ae6fbe2a5e1d07f2eafcac79ba3306749` | feat(32-01): add WAITING_FOR_IMPORT enum value across full pipeline | unknown | unprocessed | — | — | — | — | — | — |
| `db1115c28351813eb02cd754d5561723da0c7816` | docs(32-01): complete cosmetic fixes plan | unknown | unprocessed | — | — | — | — | — | — |
| `4ccc650be532e81f0a56aeef1f4c775a82bb7c5a` | docs(phase-32): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `73f647cfc09135a6f3fdd25271bafefd5ae7ee7f` | chore: complete v2.0 milestone | unknown | unprocessed | — | — | — | — | — | — |
| `5975b9f10520a4b7450a61fd3d207946a807f088` | fix: pass webhook_manager to Controller in integration tests | unknown | unprocessed | — | — | — | — | — | — |
| `b66401c62a144a144a768b12b6351da480dc46ae` | fix: pass webhook_manager to WebAppBuilder in web integration tests | unknown | unprocessed | — | — | — | — | — | — |
| `650c42de4276b5214112a1d9eae1dca90ebd5bf5` | wip: v2.0 milestone completion paused — awaiting CI + tag move | unknown | unprocessed | — | — | — | — | — | — |
| `31e2aae534f0937ed1ff9d2151492bb4dfe0f32d` | fix: dark mode for sidebar/action bars/selections + toast replay on restart | unknown | unprocessed | — | — | — | — | — | — |
| `4a83863dc0e930710cfa24e3aa5d2757fd7b755b` | fix: match webhook imports against child files, not just root-level names | unknown | unprocessed | — | — | — | — | — | — |
| `066c40572f6698aa5312dbd8cc1fbc5ecf5a6db5` | chore: bump version to 2.0.1 | unknown | unprocessed | — | — | — | — | — | — |
| `fab7829417c2322c0fd2d8cf29b458ea5c5e5198` | docs: update changelog and install docs for v2.0.1 | unknown | unprocessed | — | — | — | — | — | — |
| `d2de00c27589f38e2a4c607072eb21f92c37b5a6` | chore: archive v2.0.1 hotfix — webhook child file matching | unknown | unprocessed | — | — | — | — | — | — |
| `176299aef64a1f829ca1622497ae9df931ec297b` | docs: resolve debug session webhook-import-delete-broken | unknown | unprocessed | — | — | — | — | — | — |
| `3d2a3a2e624dba6d7b937dde55a5453cae7a92d6` | chore: start v3.0 Terminal UI Overhaul milestone | unknown | unprocessed | — | — | — | — | — | — |
| `eb951ec32200687b093ba60f877fe0c7d29c024a` | docs: define v3.0 requirements — 21 Terminal UI Overhaul requirements | unknown | unprocessed | — | — | — | — | — | — |
| `1595638c93a4b77f4d84345c8a7817a03f480458` | docs: define v3.0 roadmap — 5 phases (33-37), 21 requirements mapped | unknown | unprocessed | — | — | — | — | — | — |
| `06f978ab1802876e328c1996c8d791ab35451a99` | docs(33): research phase foundation | unknown | unprocessed | — | — | — | — | — | — |
| `771ebe206f1b9fc175b17928235d2febf8828704` | docs(33): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `ef728cc212bb83954e746203adfc04c545fccf86` | feat(33-01): load Google Fonts, hardcode dark theme in index.html | unknown | unprocessed | — | — | — | — | — | — |
| `6865ea03563937cf8e25e1e56aba3782c0ee8324` | feat(33-01): replace Bootstrap SCSS variables with Terminal/Hacker palette | unknown | unprocessed | — | — | — | — | — | — |
| `bc9c3991eb291364a9347cacd56b8d041cf165c8` | docs(33-01): complete foundation plan 01 — Terminal palette SCSS + font loading | unknown | unprocessed | — | — | — | — | — | — |
| `42d75b039b7feef31a1508b2327a4f1ce616a9aa` | feat(33-02): dark-only overrides + custom scrollbars | unknown | unprocessed | — | — | — | — | — | — |
| `29e7d5d0a995e64fcc3e26e74ba9443fab6d249d` | feat(33-02): Terminal CSS custom properties + CRT overlay + keyframes | unknown | unprocessed | — | — | — | — | — | — |
| `7ea6763672e35f2ac4cc3be61f60d5a1293cf46c` | docs(33-02): complete Terminal CSS custom properties plan | unknown | unprocessed | — | — | — | — | — | — |
| `678e217ed36ddd51302bd34f670b95d601190fda` | fix(33): force ThemeService to dark-only, stop localStorage override | unknown | unprocessed | — | — | — | — | — | — |
| `945688ae8a3eaaaf62005e14261a80e5cf5b4be6` | fix(33): set input-btn-font-family so all interactive elements use IBM Plex Sans | unknown | unprocessed | — | — | — | — | — | — |
| `c5ea4b2387a8b7ce5c8809058c936efe98da6a98` | docs(33-03): complete visual verification plan — Phase 33 Foundation done | unknown | unprocessed | — | — | — | — | — | — |
| `96f8e361aa03746bbc168ecc3620e444b45f77dc` | docs(phase-33): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `72036b06f026be3378c4475421d7e55179a30e20` | docs(state): advance to phase 34, fix progress bar | unknown | unprocessed | — | — | — | — | — | — |
| `ed25892fbf208207d917ef8bf7fff686b56704dc` | docs(34): research phase shell | unknown | unprocessed | — | — | — | — | — | — |
| `af7f17b89936d76c6b974019647d10a8c8195607` | docs(34): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `f64325e331a0c46bb566693252fed475e8653b3b` | feat(34-01): restructure sidebar HTML and SCSS for icon-rail layout | unknown | unprocessed | — | — | — | — | — | — |
| `9dd1d796382ab7dfffbc4012804cf5d27e15e33d` | feat(34-01): implement icon-rail CSS on large screens, preserve mobile behavior | unknown | unprocessed | — | — | — | — | — | — |
| `b4bcaecf9f1409dd2b97da7ac06385e07b44d9d3` | docs(34-01): complete icon-rail sidebar plan — Phase 34 Plan 1/2 done | unknown | unprocessed | — | — | — | — | — | — |
| `a32dfad7d1efa27f912c66b657658ccdba1e1947` | feat(34-02): add prompt indicator and version footer to sidebar | unknown | unprocessed | — | — | — | — | — | — |
| `7015357020d9f5b1c60b61117e00e75b37f18a29` | fix(34-02): icon visibility and label hover across component boundary | unknown | unprocessed | — | — | — | — | — | — |
| `4974f60d51db6ea031f420b551a510b27d2ee052` | docs(34-02): complete prompt indicator + version footer plan — Phase 34 Plan 2/2 done | unknown | unprocessed | — | — | — | — | — | — |
| `2ce7551f0e2c6954fb8588e7103b8765e1e7fd51` | docs(phase-34): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `666d355e67fb580264dac23e804d2891a635a22a` | docs(35): research phase dashboard | unknown | unprocessed | — | — | — | — | — | — |
| `b3039a9afd5c0da229ebbe206b691983ce41abda` | docs(35): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `093033b64d819ecee8501917e4e894794d306990` | feat(35-02): add @HostBinding hostClass and statusDotClass getters | unknown | unprocessed | — | — | — | — | — | — |
| `72699bdf4eda452bc5e469b8b45d5792e9f31144` | feat(35-02): replace status SVGs with dots, add borders and glow | unknown | unprocessed | — | — | — | — | — | — |
| `ea6720ac39d0c70e4b54ef294d35f37bdc2a44de` | docs(35-01): complete terminal prompt search icon plan — Phase 35 Plan 1/3 done | unknown | unprocessed | — | — | — | — | — | — |
| `4c2f5eb3978709f40c52442cb3158c376b3bef10` | docs(35-02): complete status borders, glow animation, and CSS dots plan | unknown | unprocessed | — | — | — | — | — | — |
| `93f1a0f6ef65f4346eeb661541df58da7f33bc44` | feat(35-03): add ASCII progress bar replacing Bootstrap progress | unknown | unprocessed | — | — | — | — | — | — |
| `b9c232c7e4b328beb656a2b848cc7657fbac1ab0` | feat(35-03): convert action buttons to ghost outline style with glow hover | unknown | unprocessed | — | — | — | — | — | — |
| `11d6200c716b9d895c78f2fbdefe3f82da496788` | docs(35-03): complete ASCII progress bars and ghost buttons plan — Phase 35 done | unknown | unprocessed | — | — | — | — | — | — |
| `2a94269a7777416b719ce61048d81de624ebd6d4` | docs(phase-35): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `4b7f767d11f3797f53b77cb522f68c9db93b833e` | docs(36): research phase secondary-pages domain | unknown | unprocessed | — | — | — | — | — | — |
| `a2a502d73770c6773c889ac4f2441d5a971de8de` | docs(36): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `b7fdff1a996a44669c62634cf42efe0f4d1b8a69` | feat(36-01): terminal-style Settings headers and remove Appearance card | unknown | unprocessed | — | — | — | — | — | — |
| `d8570982918f1ab12c644d4735a779e888b45916` | feat(36-02): terminal-pure log level colors and status message styling | unknown | unprocessed | — | — | — | — | — | — |
| `0bdeef590c10070b7c81fc6fd65703ea6b09ebe5` | feat(36-01): AutoQueue ghost buttons, Fira Code patterns, terminal description | unknown | unprocessed | — | — | — | — | — | — |
| `12a05c863a966f58bc55b2a8c27a5072ac999eb8` | feat(36-02): ASCII art About page with terminal markers and monospace version | unknown | unprocessed | — | — | — | — | — | — |
| `0e1a511ec207bc3885739e8d3d39dd3be6a555f6` | docs(36-01): complete secondary-pages plan 01 | unknown | unprocessed | — | — | — | — | — | — |
| `7dd5406376cb4dd32aec78e7eab7eb06b5b690f4` | docs(36-02): complete terminal log colors and ASCII art About plan — Phase 36 done | unknown | unprocessed | — | — | — | — | — | — |
| `23ca64aedcbc137ec2b06a575d44853e2ad10458` | docs(phase-36): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `0e5e0ad0a17d4c63da4885450c063637a6f8d577` | docs(37): research phase theme cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `c755220ca60ef40d78f27441b8c42addd92ddaaa` | docs(37): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `869112297afd6e30086023ea9c996b3101606bca` | chore(37-01): delete ThemeService, theme types, and theme test files | unknown | unprocessed | — | — | — | — | — | — |
| `5c0e469b3fb9c824f3a488ca528728f7ab522765` | chore(37-01): remove ThemeService from app.config.ts | unknown | unprocessed | — | — | — | — | — | — |
| `8039874ee2cfbe4bb200b0b517487874760f725c` | docs(37-01): complete theme-cleanup plan 01 — deleted ThemeService and all dead code | unknown | unprocessed | — | — | — | — | — | — |
| `1d32a97527398909e4ac34d8a1f7b174dabfe82e` | docs(phase-37): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `b9e0211fde1f6cbfe91591dc172da72e483ab0b4` | docs: add v3.0 milestone audit report | unknown | unprocessed | — | — | — | — | — | — |
| `2695d851d6f3384e883be58dba23a39a5a022dd0` | docs(roadmap): add gap closure phase 38 | unknown | unprocessed | — | — | — | — | — | — |
| `486763ae85f1073589cfce762ca03623ad5f6c0b` | docs(38): research terminal polish traceability phase | unknown | unprocessed | — | — | — | — | — | — |
| `a79c7bb575187f04b5560777a2fac54d0a74876e` | docs(38): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `96f7d86ba8ba3adee903d7da75da61c8cbe70581` | fix(38-01): fix CSS variable typo in sidebar version text and update requirements traceability | unknown | unprocessed | — | — | — | — | — | — |
| `16156606165cc3ccdf4772f895f639fb184d65fa` | docs(38-01): complete terminal polish & traceability plan summary and state update | unknown | unprocessed | — | — | — | — | — | — |
| `0926853b5bf2432659bf5db22aa6d06a46ab1249` | docs(phase-38): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `68c1406ff3c15dab9e958e6d330c9a3c59a7d642` | docs: v3.0 milestone audit — passed (21/21 requirements, 6/6 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `cf0a5fcbfb885805aa151d9b333bfe5def327b77` | chore: archive v3.0 Terminal UI Overhaul milestone | unknown | unprocessed | — | — | — | — | — | — |
| `10471415471d7dae0be5468c564a46df241d703e` | fix(e2e): update selectors for v3.0 terminal UI class changes | unknown | unprocessed | — | — | — | — | — | — |
| `ab15f80e5db0e69ea3ba5af9cde40572982bc9ab` | docs: start milestone v3.1 Harden & Fix | unknown | unprocessed | — | — | — | — | — | — |
| `a4f6c76e6f592816b19c18f45ac0ff90dbdf2601` | docs: define milestone v3.1 requirements | unknown | unprocessed | — | — | — | — | — | — |
| `b8e767537a9008471f14f44ec783e6059a9ea496` | docs: create milestone v3.1 roadmap (7 phases) | unknown | unprocessed | — | — | — | — | — | — |
| `15dad1544485baffd91b2fa5ced260c707b1c07f` | docs(39): capture phase context | unknown | unprocessed | — | — | — | — | — | — |
| `e3d9e70eb256c4a4a166ccf2d6dad52fee2eecce` | docs(39): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `f6643db8b5211276552014abdf9061deac3a1b98` | fix(39-01): remove committed RSA private key and protect with .gitignore | unknown | unprocessed | — | — | — | — | — | — |
| `108018f28ae67724aeb46363623abb73694cb69d` | feat(39-02): replace pickle with JSON in SystemFile and scan_fs | unknown | unprocessed | — | — | — | — | — | — |
| `e34ba5e11f298509463d5697835f71dfacc29776` | fix(39-01): harden SSH host key verification across all connection paths | unknown | unprocessed | — | — | — | — | — | — |
| `abef04aff6188b5bef0c3faf4c5192e782e93b4c` | feat(39-02): migrate remote_scanner to JSON deserialization | unknown | unprocessed | — | — | — | — | — | — |
| `62cf83a7e5fca62c7388548a02ef9f874c0c8601` | docs(39-01): complete critical SSH security hardening plan | unknown | unprocessed | — | — | — | — | — | — |
| `44294c9c9b095c4fb8b5345d6c53e1570481fbc5` | docs(39-02): complete JSON migration (pickle RCE elimination) plan | unknown | unprocessed | — | — | — | — | — | — |
| `f5fe429248b04515a87067ed22ab41cac9c46f2a` | docs(phase-39): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `3c2a330d9a261b4c46f72a493ccfbd95f0f3fa7d` | docs(40): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `0a4a4108f98e201a61e6d60fc1c5588afcccee5f` | feat(40-01): redact sensitive fields from config GET response | unknown | unprocessed | — | — | — | — | — | — |
| `b9a32201a1d422d49e50e3dec5d1d1619529cb5f` | feat(40-01): scrub passwords from SSE log stream records | unknown | unprocessed | — | — | — | — | — | — |
| `6e680df653ce3396c8a3787031d88de2111ad2f2` | feat(40-02): add SSRF protection and error sanitization to test-connection endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `a92af56ed211c7e7eb383ff23637010d1f591f29` | feat(40-03): add HMAC authentication to webhook endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `826c7ff8b952a00941e93214bc341914c813dd6b` | docs(40-01): complete credential endpoint security plan | unknown | unprocessed | — | — | — | — | — | — |
| `4c485d921d97cc454841ba84f4a2b55d4b88a9b9` | feat(40-03): add security headers to all API responses | unknown | unprocessed | — | — | — | — | — | — |
| `492944f4f584effe43fcc3408ce2f6b72925ee6e` | feat(40-02): escape shell metacharacters in DeleteRemoteProcess using shlex.quote | unknown | unprocessed | — | — | — | — | — | — |
| `73d04b125ab7edfaf55b6ffc8aa205d4aa27eecc` | docs(40-03): complete HMAC webhook auth and security headers plan | unknown | unprocessed | — | — | — | — | — | — |
| `03a9c085d00b37066d5e4878e1240277dc423b1a` | docs(40-02): complete SSRF protection + shell escaping + error sanitization plan | unknown | unprocessed | — | — | — | — | — | — |
| `ad18fcd3de8f5eb981fddc2dd1c8d26d0f5de411` | docs(phase-40): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `f6d82c40638d051c07bd0c65f684a58fea93dd29` | docs(41): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `f5e54875456eceb84ac70dece6275a0486022527` | fix(41-01): add model lock to auto-delete callback and webhook import checks | unknown | unprocessed | — | — | — | — | — | — |
| `713825d930552dd30814a6daaf90194c871f0a44` | fix(41-02): thread-safe queue access and listener lock in ExtractDispatch | unknown | unprocessed | — | — | — | — | — | — |
| `4c1bbabd592f8afc1af21e1bc86aa00635024f94` | test(41-01): add thread-safety tests for auto-delete and webhook import locks | unknown | unprocessed | — | — | — | — | — | — |
| `5e2a62c7b6108b0abb1153fdbbf6588a858646a8` | test(41-02): add thread-safety tests for ExtractDispatch queue mutex and copy-under-lock | unknown | unprocessed | — | — | — | — | — | — |
| `5d321c8d145d5d871211f2a671d694e3161805d9` | docs(41-01): complete thread-safety model lock plan | unknown | unprocessed | — | — | — | — | — | — |
| `248533df05dde4265687ea087a6213d38af5fb5e` | docs(41-02): complete ExtractDispatch queue mutex and copy-under-lock plan | unknown | unprocessed | — | — | — | — | — | — |
| `be53b866d0768f9b5606323ca8a8b96ee8467c3b` | docs(phase-41): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `57ec9ee1ae031129d8fdba17c0da6202cb8ba45e` | docs(42): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `e736b6932d22afaf8a05a1e6e5ec7906dd1b6818` | fix(42-02): guard SSE dispatch against unknown event names (CRASH-04) | unknown | unprocessed | — | — | — | — | — | — |
| `05a00038c6e383acb842b9fef3ad0113287bbe4c` | fix(42-03): add bounded 30s timeout to all individual action endpoint waits | unknown | unprocessed | — | — | — | — | — | — |
| `52104364e91c70af1cd797c13d9d0241c635d23b` | fix(42-01): fix propagate_exception redundant raise and WebhookManager bare except | unknown | unprocessed | — | — | — | — | — | — |
| `a7122fc164c1c64bab0bc3049007eac3d89e2750` | fix(42-02): wrap JSON.parse in try/catch across all SSE stream services (CRASH-05) | unknown | unprocessed | — | — | — | — | — | — |
| `d2e4befb839e10f50f1cabb7a31a873b27b3f9cc` | fix(42-01): guard _estimate_root_eta against None remote_size (CRASH-02) | unknown | unprocessed | — | — | — | — | — | — |
| `7b2cdd37129df37f1a116d790f921046534eab53` | docs(42-03): complete bounded action timeout plan | unknown | unprocessed | — | — | — | — | — | — |
| `0bac5618cc9156379dcabf85897b0140826e2ad1` | docs(42-01): complete crash-prevention plan 01 — propagate_exception, ETA guard, bare except | unknown | unprocessed | — | — | — | — | — | — |
| `eeb9c21d633627e52d87135cea8717c9e9ce88a3` | docs(42-02): complete Angular SSE crash prevention plan | unknown | unprocessed | — | — | — | — | — | — |
| `42e2267fbe897cf410796c527379e9da70bcd5f6` | docs(phase-42): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `bcde805f051c0f56c261c5023ca63f58ff0f77a8` | docs(43): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `67179ea469f7af57eb1313dfda8ca052b4d62387` | fix(43-02): fix AppComponent subscription leaks with takeUntil/destroy$ | unknown | unprocessed | — | — | — | — | — | — |
| `8271bd6a4b0a9abcb5b240935485ad20f8cda822` | fix(43-01): sanitize ConfirmModalService innerHTML inputs to prevent XSS | unknown | unprocessed | — | — | — | — | — | — |
| `7631bfba10ef76d193b09e692e0188b931e82404` | fix(43-03): fix AutoQueueService stale index and StreamDispatchService timer cleanup | unknown | unprocessed | — | — | — | — | — | — |
| `b1b7ec92d25d3bcfbe34f5933d1c600994da2c32` | fix(43-02): fix SettingsPage and AutoQueuePage subscription leaks | unknown | unprocessed | — | — | — | — | — | — |
| `5664431f0cce74d0758bdaf88b917e9cf6d717db` | refactor(43-01): replace nested subscribe anti-pattern in RestService with pipe operators | unknown | unprocessed | — | — | — | — | — | — |
| `03ee46c14f9a0ee7761f236cbd22639a9a274f11` | refactor(43-03): consolidate file-options async pipe to single subscription | unknown | unprocessed | — | — | — | — | — | — |
| `ae642f635393805bc16d9e5e7d66cd09fe9da325` | docs(43-02): complete subscription leak fix plan — takeUntil/destroy$ in 3 components | unknown | unprocessed | — | — | — | — | — | — |
| `c3215d8f8f1eb4cfa55dd2be4bd6383cb950e4a7` | docs(43-01): complete XSS fix and RestService pipe refactor plan | unknown | unprocessed | — | — | — | — | — | — |
| `53f1748e368b49fabe806a0309c3da7e969f406a` | docs(43-03): complete stale index fix, timer cleanup, async pipe consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `2d54902c1abf5b7e2818e8832663f2c36adf3070` | docs(phase-43): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `758ab1178f88ebceab419518aebd2442d97e6425` | docs(44): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `bb283e65205d262fbb214a6bb08286add158b70c` | fix(44-01): replace distutils.strtobool and fix type comparisons | unknown | unprocessed | — | — | — | — | — | — |
| `a50a6eca15fb183798faaeb2178a1a4a95789520` | fix(44-03): convert mutation endpoints to POST/DELETE; instance-level rate limiter; improve type annotations | unknown | unprocessed | — | — | — | — | — | — |
| `a53869eee59348cb727541099d399692f38a5df9` | fix(44-02): add sleep to busy-poll loop and log TIMEOUT in lftp.py | unknown | unprocessed | — | — | — | — | — | — |
| `9b4e3b6f78d0659fef305c8503ea513a56e46d24` | feat(44-05): document hardcoded test credentials as intentional test-only values | unknown | unprocessed | — | — | — | — | — | — |
| `714dcaf2c566cead4b9c751385fce993865e04ba` | feat(44-03): update Angular frontend to use POST/DELETE for mutation endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `aa7593718bbbbf6a1115e098b6c6ab18884f6888` | docs(44-01): complete distutils replacement, isinstance() migration, ModelFile unfreeze() plan | unknown | unprocessed | — | — | — | — | — | — |
| `258d81d012c67f5613efbbd37dc8ef15f7909b92` | docs(44-03): complete HTTP method correctness and rate limiter isolation plan | unknown | unprocessed | — | — | — | — | — | — |
| `fcc46821baf9ba59d4eb14066987071c6d6abcf9` | docs(44-05): complete test credential documentation plan | unknown | unprocessed | — | — | — | — | — | — |
| `4b5394687c5491d332c97522906156ee67858fd0` | docs(44-02): complete pexpect arg list, TIMEOUT logging, busy-poll sleep plan | unknown | unprocessed | — | — | — | — | — | — |
| `65dc7fe41374470564f2952dd0182cdcc1fb019d` | fix(44-04): correct __downloaded_files type to BoundedOrderedSet; fix directory DOWNLOADED edge case | unknown | unprocessed | — | — | — | — | — | — |
| `48f9a68e3b8a5cf7d61dc7b1873d12971dc473fa` | refactor(44-04): consolidate import_status code paths into _set_import_status helper | unknown | unprocessed | — | — | — | — | — | — |
| `8f0b48b896a06c02a79943e8404739fbb03dfe34` | docs(44-04): complete type semantics fix and import status consolidation plan | unknown | unprocessed | — | — | — | — | — | — |
| `c22bfcb8729d9be219047399f3783376ccabd165` | docs(44-04): mark CODE-07, CODE-10, CODE-12 requirements complete | unknown | unprocessed | — | — | — | — | — | — |
| `4f182fc1d6a047cf4f9a0768b540beceab555248` | docs(phase-44): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `eebf86ad789b09319e072fb6d01bd49cab43f093` | docs(45): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `e3a074eea2557f15162bbe0538d6f7f86a363135` | docs(45-01): update CLAUDE.md version reference and API response codes | unknown | unprocessed | — | — | — | — | — | — |
| `fdb2b7f855949c49800ba541a81774cffdd8ece1` | feat(45-02): add keyboard focus trap and focus restoration to ConfirmModalService | unknown | unprocessed | — | — | — | — | — | — |
| `2fa98d1ba374796413b67ed6fef2d706651aa767` | test(45-02): add focus trap and ARIA attribute tests for ConfirmModalService | unknown | unprocessed | — | — | — | — | — | — |
| `3600465bb4bb79a47560bfafc91e46af3b40409c` | docs(45-01): complete documentation-accessibility plan | unknown | unprocessed | — | — | — | — | — | — |
| `16637d7320ea7e637102ac98958d902b4a65f87d` | docs(45-02): complete confirm modal focus trap plan | unknown | unprocessed | — | — | — | — | — | — |
| `d288d60cf0ec445bbd5ea468be78f293f7525050` | docs(45-02): add self-check and finalize SUMMARY.md | unknown | unprocessed | — | — | — | — | — | — |
| `801c437c969ccfb9d2c2e696107212c360d385d6` | docs(45-03): complete keyboard navigation and ARIA attributes plan | unknown | unprocessed | — | — | — | — | — | — |
| `a644ef80c53307b17b8daf61a42fb16543725b20` | docs(45-03): add self-check to SUMMARY.md | unknown | unprocessed | — | — | — | — | — | — |
| `4bdeb328cdc567802e6ebce799b9542ef2882dbc` | docs(phase-45): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `5204d3b4617e86a197841fa8084010c7abf9c1d4` | docs(v3.1): milestone audit — 44/44 requirements satisfied, 7/7 phases passed | unknown | unprocessed | — | — | — | — | — | — |
| `2e21c54a035b08d1c16dbff616c90e9417547256` | docs: fix stale SEC-04/SEC-05 traceability status (Pending→Complete) | unknown | unprocessed | — | — | — | — | — | — |
| `7dfff3ce6c91651a90f7cd1affc073d6e31f42f1` | docs(v3.1): add Phase 46 — 12 code review fixes from deep review | unknown | unprocessed | — | — | — | — | — | — |
| `8bacdcc20e226f692539302ebc521569d44aaa55` | docs(46-code-review-fixes): create phase plan | unknown | unprocessed | — | — | — | — | — | — |
| `904837730abbbb54595191cfba85f38147537c56` | fix(46-01): redact webhook_secret in config API and use getMessage() for log redaction | unknown | unprocessed | — | — | — | — | — | — |
| `9365743d64bef6997f97e40b1cf8929ec6341334` | fix(46-03): full focus trap and XSS sanitization in confirm modal | unknown | unprocessed | — | — | — | — | — | — |
| `d9ed3f99d71c5b7a7e33abf7dfeb65872ce9bd04` | fix(46-04): clear _reconnectTimer before reassignment; fix unknown-event test (CR-06, CR-08) | unknown | unprocessed | — | — | — | — | — | — |
| `b2d5533452fe55edda941aa5efa7805a07f417b3` | docs(46-01): complete code-review-fixes plan 01 - webhook_secret redaction + getMessage() log fix | unknown | unprocessed | — | — | — | — | — | — |
| `b53fe7d5305ad51eecb99a8670779f9fe564b219` | fix(46-04): LogService injects LoggerService; RestService extracts error helpers (CR-09, CR-11) | unknown | unprocessed | — | — | — | — | — | — |
| `784e1ff023e73304c17a2587873aafff5383ca30` | fix(46-02): atomic extract() duplicate-check+insert and resilient worker finally | unknown | unprocessed | — | — | — | — | — | — |
| `a0dfd21050e11376a124f637034fd7b9b1ad3342` | docs(46-04): complete code review fixes plan 04 (CR-06, CR-08, CR-09, CR-11) | unknown | unprocessed | — | — | — | — | — | — |
| `8daf2218cb7707aec2702c9bd382bdc3de647763` | fix(46-02): rename unfreeze() to _unfreeze() and narrow _set_import_status except scope | unknown | unprocessed | — | — | — | — | — | — |
| `469c0758efdee2a210592f72a5e6111f6c553e24` | docs(46-02): complete code-review-fixes plan 02 summary and state update | unknown | unprocessed | — | — | — | — | — | — |
| `ab1759d45111b14dbec5d3602d64bf741287c1b8` | docs(phase-46): complete phase execution | unknown | unprocessed | — | — | — | — | — | — |
| `2eba5bfd9a7931540d5b2b8be52125013d504ff4` | chore: complete v3.1 Harden & Fix milestone | unknown | unprocessed | — | — | — | — | — | — |
| `1f0fa87fbafdf7d8b5f8bf72bdf13b5076df0489` | fix: update integration tests to use POST/DELETE for mutation endpoints | unknown | unprocessed | — | — | — | — | — | — |
| `52b72a6cc9147bedbdd9eb00f3d432a74870c544` | fix(e2e): add explicit z-index to confirmation modal to prevent sidebar overlap | unknown | unprocessed | — | — | — | — | — | — |
| `31889adf1469efadb80d1ec2aab3e82f39453b75` | fix(e2e): add explicit position:fixed to confirmation modal and backdrop | unknown | unprocessed | — | — | — | — | — | — |
| `0b26f0ad2df0f1b2aa0515f39f93a14ae3f2534b` | fix: prevent name column from being squeezed to zero on medium screens | unknown | unprocessed | — | — | — | — | — | — |
| `a48763dd0da2967518b69fa6a0a16d394c60623c` | fix: raise timestamp column breakpoint to 1200px to prevent name squeeze | unknown | unprocessed | — | — | — | — | — | — |
| `246c0639d4a7c7aeb0c1ddcbb4ef0947d32f6882` | fix: resolve CSP violations blocking GitHub API and inline event handlers | unknown | unprocessed | — | — | — | — | — | — |
| `8c4edb27e9ef60ced0dfe3c3dfc398c91fe1e03e` | fix: replace css-element-queries with native ResizeObserver to fix CSP violation | unknown | unprocessed | — | — | — | — | — | — |
| `6a8024da060a68fe4dd8b25c15a67750094916c2` | chore: add todo for e2e CSP violation detection | unknown | unprocessed | — | — | — | — | — | — |
| `0e6370eae00cd01353b014f9b2b34d3581103e37` | fix: add unsafe-inline to script-src CSP for inline event handler compatibility | unknown | unprocessed | — | — | — | — | — | — |
| `a8561cdc318460de32de082e3cf33f6b6a0093cb` | chore: bump version to 3.1.2 | unknown | unprocessed | — | — | — | — | — | — |
