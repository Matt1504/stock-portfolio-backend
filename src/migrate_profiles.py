"""Assign existing records to one profile without recreating the database.

Dry-run by default. Importing this module never connects to the real database.
"""
import argparse
from models.models import Profile, Platform, Transaction, ContributionLimit


def migrate_profiles(name=None, profile_id=None, apply=False):
    profile = Profile.objects.get(pk=profile_id) if profile_id else None
    if profile is None:
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise ValueError("Provide a profile name between 1 and 100 characters.")
        matches = list(Profile.objects(name=name))
        if len(matches) > 1:
            raise ValueError("More than one profile has this name. Use --profile-id.")
        profile = matches[0] if matches else None

    platforms = {record["_id"]: record for record in Platform._get_collection().find()}
    profile_ids = set(Profile._get_collection().distinct("_id"))
    issues = []
    for platform in platforms.values():
        if platform.get("profile") is not None and platform["profile"] not in profile_ids:
            issues.append("Platform {} references a missing profile".format(platform["_id"]))
    for transaction in Transaction._get_collection().find():
        platform = platforms.get(transaction.get("platform"))
        if platform is None:
            issues.append("Transaction {} has a missing platform".format(transaction["_id"]))
        elif transaction.get("account") != platform.get("account"):
            issues.append("Transaction {} has an account/platform mismatch".format(transaction["_id"]))
    for limit in ContributionLimit._get_collection().find():
        if limit.get("profile") is not None and limit["profile"] not in profile_ids:
            issues.append("Contribution limit {} references a missing profile".format(limit["_id"]))
    if issues:
        raise ValueError("Migration stopped before writing:\n" + "\n".join(issues))

    # {profile: None} matches missing and null fields. Existing ownership is retained.
    counts = {"platforms": Platform._get_collection().count_documents({"profile": None}),
              "contribution_limits": ContributionLimit._get_collection().count_documents({"profile": None})}
    if apply:
        profile = profile or Profile(name=name).save()
        Platform._get_collection().update_many({"profile": None}, {"$set": {"profile": profile.id}})
        ContributionLimit._get_collection().update_many({"profile": None}, {"$set": {"profile": profile.id}})
        from cache.queries import invalidate_transactions
        invalidate_transactions()
    return {**counts, "profile_id": str(profile.id) if profile else None, "applied": apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="Name of the profile that owns existing records")
    target.add_argument("--profile-id", help="Assign missing ownership to an existing profile")
    parser.add_argument("--apply", action="store_true", help="Write changes; otherwise only audit")
    args = parser.parse_args()
    from database.database import client  # Connect only when explicitly running the command.
    try:
        result = migrate_profiles(args.name, args.profile_id, args.apply)
        print("Applied" if result["applied"] else "Dry run; no records changed")
        print("Platforms: {}; contribution limits: {}; profile: {}".format(
            result["platforms"], result["contribution_limits"], result["profile_id"] or "will be created"))
        if args.apply:
            print("Rotate CACHE_NAMESPACE before restarting the application.")
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    finally:
        client.close()


if __name__ == "__main__":
    main()
