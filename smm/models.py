"""Pydantic models and the LangGraph state."""

from __future__ import annotations

from typing_extensions import TypedDict

from pydantic import BaseModel, Field

DEFAULT_CONTENT_OBJECTIVE = "Not specified"


class profile_metadata(BaseModel):
    username: str = Field(description="The username of the Instagram profile")
    description: str = Field(description="The description or bio of the Instagram profile")
    reels: list[str] = Field(default_factory=list, description="The urls of the reels of the Instagram profile")


class smm_metadata(BaseModel):
    subject: profile_metadata = Field(description="The subject of the Instagram profile")
    competitors: list[profile_metadata] = Field(description="The competitors of the Instagram profile")
    important_accounts: list[profile_metadata] = Field(
        default_factory=list,
        description="Accounts the creator wants their content to look/feel exactly like",
    )
    content_objective: str = Field(
        default=DEFAULT_CONTENT_OBJECTIVE,
        description=(
            "The creator's content goal exactly as written (type + description), "
            f"or '{DEFAULT_CONTENT_OBJECTIVE}' if absent"
        ),
    )


class insta_profile(BaseModel):
    username: str = Field(description="The username of the Instagram profile")
    full_name: str = Field(description="The full/display name of the Instagram profile")
    followers: int = Field(description="The number of followers of the Instagram profile")
    following: int = Field(description="The number of people the user is following")
    posts_count: int = Field(description="The number of posts the user has made")
    bio: str = Field(description="The bio of the Instagram profile")
    profile_picture: str = Field(description="The profile picture of the Instagram profile")
    posts: list[str] = Field(description="The urls of the recent posts/reels selected for analysis")


class GraphState(TypedDict, total=False):
    """State carried through the SMM pipeline."""

    input_text: str
    metadata: dict
    subject: dict
    competitors: list
    important_accounts: list
    content_dna_intake: str
    content_objective: str
    dna_bundle: str
    performance_data: str
    prompt_1_result: str
    prompt_2_result: str
    prompt_3_result: str
    prompt_4_result: str
    prompt_5_result: str
    prompt_6_result: str
    final_document: str
    output_txt_path: str
    output_md_path: str
    output_pdf_path: str
