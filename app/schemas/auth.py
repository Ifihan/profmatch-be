from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "password": "ChangeMe_example123",
        "confirm_password": "ChangeMe_example123",
    }})

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info):
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("passwords do not match")
        return v


class LoginRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "email": "ada@example.com",
        "password": "ChangeMe_example123",
    }})

    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"email": "ada@example.com"}})

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "token": "<paste the reset token>",
        "new_password": "ChangeMe_example456",
    }})

    token: str
    new_password: str = Field(min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {"name": "Ada Lovelace"}})

    name: str = Field(min_length=1, max_length=120)


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "password": "ChangeMe_example123",
    }})

    password: str


class RefreshRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": {
        "refresh_token": "<paste the refresh token>",
    }})

    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: str
    name: str
    email: str
    is_admin: bool
    created_at: datetime
    credit_balance: int


class SearchSummary(BaseModel):
    job_id: str
    status: str
    progress: int
    university_url: str | None = None
    research_interests: str | None = None
    total_professors_analyzed: int | None = None
    match_count: int
    created_at: datetime
