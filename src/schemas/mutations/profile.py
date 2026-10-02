import graphene
from graphql import GraphQLError
from models.profile import Profile
from type.profile import ProfileType


class CreateProfileMutation(graphene.Mutation):
    profile = graphene.Field(ProfileType)

    class Arguments:
        name = graphene.String(required=True)

    def mutate(self, info, name):
        name = name.strip()
        if not name or len(name) > 100:
            raise GraphQLError("Enter a profile name between 1 and 100 characters.")
        profile = Profile(name=name).save()
        return CreateProfileMutation(profile=profile)
