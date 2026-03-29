#!/usr/bin/env python3
"""
Test the email classifier against all observed GitLab email types.
Simulates the email dict structure that parse_emails() produces.
"""

import base64
import logging
import sys

sys.path.insert(0, ".")
from gitlab_notifier import classify_email, extract_pr_url, extract_sender_name, Classification

logger = logging.getLogger("test")
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.WARNING)  # quiet for tests

# --- Test cases derived from the actual PDF emails ---
TEST_CASES = [
    # (description, email_dict, expected_type, expected_title_contains)

    # 1. New commits pushed
    (
        "Pushed new commits",
        {
            "subject": "Re: cav-ts-apps-tools | fix(ioo): live video available on closed incident (!891)",
            "source": '<p>Mallory Benna pushed new commits to merge request <a href="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/891">!891</a></p>',
        },
        "new_commits",
        "Commits",
    ),

    # 2. Pipeline failure
    (
        "Failed pipeline",
        {
            "subject": "cav-ts-apps-tools | Failed pipeline for main | b475448f",
            "source": '<a href="https://gitlab.com/cavnue/cav-ts-apps-tools/-/pipelines/12345">Pipeline</a>',
        },
        "pipeline_failure",
        "Pipeline Failed",
    ),

    # 3. Merge request approved (rich HTML body)
    (
        "MR approved (HTML body, no subject suffix)",
        {
            "subject": "Re: Deployments | feat: bump apps-tools (!374)",
            "source": 'Merge request was approved (3/2) <a href="https://gitlab.com/cavnue/deployments/-/merge_requests/374">!374</a> was approved by Ben Hager',
        },
        "approved",
        "Approved",
    ),

    # 4. MR closed
    (
        "MR closed",
        {
            "subject": "Re: cav-ts-apps-tools | feat(mission-control): state + environment selection and event-bus config refinement (!901)",
            "source": 'Merge request <a href="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/901">!901</a> was closed by Isaac McRobie',
        },
        "closed",
        "Closed",
    ),

    # 5. MR merged (text body)
    (
        "MR merged (text body)",
        {
            "subject": "Re: cav-ts-apps-tools | fix: missing insight clips for incidents with known insights (!903)",
            "source": 'Merge request <a href="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/903">!903</a> was merged\nBranches: bug/PLAT-7762/insights-no-clips to main\nAuthor: Mallory Benna',
        },
        "merged",
        "Merged",
    ),

    # 6. MR merged (another)
    (
        "MR merged (Deployments)",
        {
            "subject": "Re: Deployments | feat: targeting renamed slackbot token (!415)",
            "source": 'Merge request !415 was merged\nBranches: adhoc/slack-bot-rename to main\nhref="https://gitlab.com/cavnue/deployments/-/merge_requests/415"',
        },
        "merged",
        "Merged",
    ),

    # 7. MR approved (rich HTML, different project)
    (
        "MR approved (Infrastructure)",
        {
            "subject": "Re: Infrastructure | feat: scoping apps-tools slackbot token name (!193)",
            "source": 'Merge request was approved (4/2) href="https://gitlab.com/cavnue/infrastructure/-/merge_requests/193"',
        },
        "approved",
        "Approved",
    ),

    # 8. Comment on PR
    (
        "Comment on PR",
        {
            "subject": "Re: cav-ts-apps-tools | perf(ioo): improve live video stream init by 50% (!927)",
            "source": 'Mallory Benna commented:\nnice!\nhref="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/927"',
        },
        "comment",
        "Comment",
    ),

    # 9. MR approved (own PR — Author is Daniel)
    (
        "Own PR approved",
        {
            "subject": "Re: cav-ts-apps-tools | feat(ioo): incident filter design updates (!893)",
            "source": 'Merge request was approved (1/1) Merge request !893 was approved by Mallory Benna\nAuthor: Daniel Kuhlwein\nhref="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/893"',
        },
        "approved",
        "Approved",
    ),

    # 10. Deploy MR approved
    (
        "Deploy MR approved",
        {
            "subject": "Re: Deployments | deploy: ioo (!399)",
            "source": 'Merge request was approved (1/1) href="https://gitlab.com/cavnue/deployments/-/merge_requests/399"',
        },
        "approved",
        "Approved",
    ),

    # 11. Subject-line suffix: review requested
    (
        "Review requested (subject suffix)",
        {
            "subject": "Re: cav-ts-apps-tools | feat: something new (!999) (Review requested)",
            "source": 'href="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/999"',
        },
        "review_requested",
        "Review Requested",
    ),

    # 12. Subject-line suffix: merged
    (
        "Merged (subject suffix)",
        {
            "subject": "Re: cav-lib-data | feature: Add DB Users (!79) (Merged)",
            "source": 'href="https://gitlab.com/cavnue/cav-lib-data/-/merge_requests/79"',
        },
        "merged",
        "Merged",
    ),

    # 13. MR approved (cav-lib-data)
    (
        "MR approved (cav-lib-data)",
        {
            "subject": "Re: cav-lib-data | bugfix: update incident-verification table (!81)",
            "source": 'Merge request was approved (1/2) Merge request !81 was approved by Ben Hager\nhref="https://gitlab.com/cavnue/cav-lib-data/-/merge_requests/81"',
        },
        "approved",
        "Approved",
    ),

    # 14. Pipeline failure (second variant)
    (
        "Failed pipeline (cav-lib-data)",
        {
            "subject": "cav-lib-data | Failed pipeline for main | b2f2b4b5",
            "source": 'href="https://gitlab.com/cavnue/cav-lib-data/-/pipelines/67890"',
        },
        "pipeline_failure",
        "Pipeline Failed",
    ),

    # 15. Draft PR
    (
        "Draft MR update",
        {
            "subject": "Re: Deployments | Draft: feat: adding mc-backend updating mc config values (!404)",
            "source": 'Draft: feat: adding mc-backend href="https://gitlab.com/cavnue/deployments/-/merge_requests/404"',
        },
        "draft",
        "Draft",
    ),

    # 16. MR approved — real MIME base64-encoded body (the actual bug scenario)
    (
        "MR approved (MIME base64-encoded body)",
        {
            "subject": "Re: Infrastructure | feat(notification-service): add SendGrid secrets for email dispatch (!194)",
            "source": (
                "MIME-Version: 1.0\r\n"
                "Content-Type: text/html; charset=UTF-8\r\n"
                "Content-Transfer-Encoding: base64\r\n"
                "\r\n"
                + base64.b64encode(
                    b'<html><body>'
                    b'<p>Merge request was approved (2/1)</p>'
                    b'<p>Merge request <a href="https://gitlab.com/cavnue/infrastructure/-/merge_requests/194">!194</a>'
                    b' was approved by Ben Hager</p>'
                    b'</body></html>'
                ).decode()
            ),
        },
        "approved",
        "Approved",
    ),

    # 17. MR merged — MIME quoted-printable encoded body
    (
        "MR merged (MIME quoted-printable body)",
        {
            "subject": "Re: cav-ts-apps-tools | fix: some bugfix (!950)",
            "source": (
                "MIME-Version: 1.0\r\n"
                "Content-Type: text/html; charset=UTF-8\r\n"
                "Content-Transfer-Encoding: quoted-printable\r\n"
                "\r\n"
                "Merge request <a href=3D\"https://gitlab.com/cavnue/cav-ts-apps-tools/=\r\n"
                "-/merge_requests/950\">!950</a> was merged"
            ),
        },
        "merged",
        "Merged",
    ),

    # 18. Comment — MIME base64-encoded body
    (
        "Comment (MIME base64-encoded body)",
        {
            "subject": "Re: Infrastructure | feat: new feature (!200)",
            "source": (
                "MIME-Version: 1.0\r\n"
                "Content-Type: text/html; charset=UTF-8\r\n"
                "Content-Transfer-Encoding: base64\r\n"
                "\r\n"
                + base64.b64encode(
                    b'<html><body>'
                    b'<p>Ben Hager commented:</p>'
                    b'<p>Looks good to me!</p>'
                    b'<a href="https://gitlab.com/cavnue/infrastructure/-/merge_requests/200">!200</a>'
                    b'</body></html>'
                ).decode()
            ),
        },
        "comment",
        "Comment",
    ),
]


def run_tests():
    passed = 0
    failed = 0

    for desc, email, expected_type, expected_title_contains in TEST_CASES:
        result = classify_email(email, logger)

        if result is None:
            print(f"  FAIL: {desc}")
            print(f"        Expected type={expected_type}, got None")
            failed += 1
            continue

        type_ok = result.type == expected_type
        title_ok = expected_title_contains.lower() in result.title.lower()

        if type_ok and title_ok:
            print(f"  PASS: {desc} → [{result.type}] {result.title}")
            passed += 1
        else:
            print(f"  FAIL: {desc}")
            print(f"        Expected type={expected_type}, got type={result.type}")
            print(f"        Expected title containing '{expected_title_contains}', got '{result.title}'")
            failed += 1

    # --- URL extraction tests ---
    print("\n--- URL Extraction Tests ---")
    url_tests = [
        (
            "MR URL from source",
            {"source": 'blah href="https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/891" blah'},
            "https://gitlab.com/cavnue/cav-ts-apps-tools/-/merge_requests/891",
        ),
        (
            "Pipeline URL from source",
            {"source": 'blah href="https://gitlab.com/cavnue/cav-lib-data/-/pipelines/67890" blah'},
            "https://gitlab.com/cavnue/cav-lib-data/-/pipelines/67890",
        ),
    ]

    for desc, email, expected_url in url_tests:
        url = extract_pr_url(email, logger)
        if url and expected_url in url:
            print(f"  PASS: {desc} → {url}")
            passed += 1
        else:
            print(f"  FAIL: {desc}")
            print(f"        Expected URL containing '{expected_url}', got '{url}'")
            failed += 1

    # --- Sender name extraction tests ---
    print("\n--- Sender Name Extraction Tests ---")
    sender_tests = [
        # (from_field, source, expected_name)
        ('"Morgan Dykshorn (@morgan-cavnue)" <gitlab@mg.gitlab.com>', "", "Morgan Dykshorn"),
        ('Mallory Benna (@mallory-cavnue) <gitlab@mg.gitlab.com>', "", "Mallory Benna"),
        ('"Isaac McRobie (@isaac-mcrobie)" <gitlab@mg.gitlab.com>', "", "Isaac McRobie"),
        ('gitlab@mg.gitlab.com', "", ""),
        ('', "", ""),
        # Fallback: bare email in from_field, but MIME source has display name
        (
            'gitlab@mg.gitlab.com',
            'From: "Morgan Dykshorn (@morgan-cavnue)" <gitlab@mg.gitlab.com>\r\nTo: Daniel\r\n',
            "Morgan Dykshorn",
        ),
        # Fallback: empty from_field, MIME source has display name
        (
            '',
            'Return-Path: <>\r\nFrom: Mallory Benna (@mallory-cavnue) <gitlab@mg.gitlab.com>\r\nSubject: test\r\n',
            "Mallory Benna",
        ),
    ]

    for from_field, source, expected_name in sender_tests:
        name = extract_sender_name(from_field, source)
        label = f"from={from_field!r}" + (f" +source" if source else "")
        if name == expected_name:
            print(f"  PASS: {label} → {name!r}")
            passed += 1
        else:
            print(f"  FAIL: {label}")
            print(f"        Expected {expected_name!r}, got {name!r}")
            failed += 1

    # --- notify_title format tests ---
    print("\n--- Notify Title Format Tests ---")
    c1 = Classification("approved", "✅", "PR Approved", "feat: something",
                         sender_name="Morgan Dykshorn")
    c2 = Classification("pipeline_failure", "🔴", "Pipeline Failed", "main (abc123)")

    title_tests = [
        ("With sender name", c1.notify_title, "PR Approved by Morgan Dykshorn"),
        ("Without sender name", c2.notify_title, "Pipeline Failed"),
    ]

    for desc, actual, expected in title_tests:
        if actual == expected:
            print(f"  PASS: {desc} → {actual!r}")
            passed += 1
        else:
            print(f"  FAIL: {desc}")
            print(f"        Expected {expected!r}, got {actual!r}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed} tests")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed!")


if __name__ == "__main__":
    run_tests()
