from sanic import Blueprint, response
from sanic.exceptions import Unauthorized
from sanic_ext.extensions.openapi.definitions import RequestBody, Response, Parameter
from sanic.request import Request
from sanic_pydantic import webargs
from sanic_ext import openapi

from app.api.auth import UserSchema, TokenSchema, RefreshTokenSchema
from app.db.models import User, Wallet, RefreshToken

from app.services import token_validator, body_validator
from app.services.repo import SQLAlchemyRepo, UserRepo, RefreshTokenRepo

from app.utils.crypt import crypt_password, check_password
from app.utils.jwt import create_token

from app.config_reader import config

auth_router = Blueprint(name="auth", url_prefix="/auth")


@auth_router.post("/register")
@openapi.definition(
    body={"application/json": UserSchema.schema()},
    response=Response(
        response.text, status=200, description="Ссылка для активации аккаунта"),
    summary="Регистрация нового пользователя")
@body_validator(body_schema=UserSchema)
async def register_new_user(request: Request, **kwargs):
    repo: SQLAlchemyRepo = request.ctx.repo
    schema: UserSchema = request.ctx.schema

    if not await repo.get_repo(UserRepo).check_user_by_login(login=schema.login):
        schema.password = await crypt_password(password=schema.password)
        user = await repo.get_repo(UserRepo).user_registration(user_data=schema)
        url = request.app.url_for("auth.activate_account",
                                  user_id=user.id, _scheme="http",
                                  _external=True, _server=f"{config.APP_HOST}:{config.APP_PORT}")
        return response.text(url)
    raise Unauthorized(message="this login already exists")



@auth_router.get("/activate/<user_id>")
@openapi.definition(
    parameter=Parameter("user_id", int, "query"),
    response=Response(
        response.text, status=200, description="Подтверждение активации аккаунта"),
    summary="Активация аккаунта")
async def activate_account(request, user_id: int):
    """Активация аккаунта после регистрации (переход по ссылке, возвращаемой /register)"""
    repo: SQLAlchemyRepo = request.ctx.repo
    if not await repo.get_repo(UserRepo).check_user_by_id(user_id=user_id):
        raise Unauthorized(message="user with this id is not registered")

    await repo.get_repo(UserRepo).activate_account(user_id=user_id)
    return response.text("account successfully activated", status=200)


@auth_router.post("/login")
@openapi.definition(
    body={"application/json": UserSchema.schema()},
    response=Response({"application/json": TokenSchema}, status=200))
@body_validator(body_schema=UserSchema)
async def login_user(request: Request, **kwargs):
    """Авторизация по логину/паролю. Возвращает access-token и refresh-token"""
    repo: SQLAlchemyRepo = request.ctx.repo
    user_data: UserSchema = UserSchema.parse_raw(request.body)
    user = await repo.get_repo(UserRepo).get_user_by_login(login=user_data.login)

    if (not user) or (not await check_password(password=user_data.password, hash_password=user.password)):
        raise Unauthorized(message="user with this username/password is not registered")

    access_token, _ = await create_token(user_id=user.id, type_token="access")
    refresh_token, ref_exp = await create_token(user_id=user.id, type_token="refresh")
    await repo.get_repo(RefreshTokenRepo).save_refresh_token(
        token=refresh_token, exp=ref_exp)
    return response.json(TokenSchema(**{"access_token": access_token, "refresh_token": refresh_token}))


@auth_router.post("/refresh_token")
@openapi.definition(
    parameter=Parameter("Authorization", str, "header"),
    body={"application/json": RefreshTokenSchema.schema()},
    summary="Обновление пары access-token/refresh-token по истечению срока старого access-token",
    response=Response({"application/json": TokenSchema}, status=200))
@token_validator
async def update_token(request: Request):
    """Обновление пары access-token/refresh-token по истечению срока старого access-token"""
    repo: SQLAlchemyRepo = request.ctx.repo
    token: RefreshToken = await repo.get_repo(RefreshTokenRepo).get_refresh_token(
        token=request.headers.get("Authorization"))

    if not token:
        raise Unauthorized(message="Unknown token")

    access_token, _ = await create_token(user_id=request.ctx.user.id, type_token="access")
    refresh_token, exp = await create_token(user_id=request.ctx.user.id, type_token="refresh")
    await repo.get_repo(RefreshTokenRepo).update_refresh_token(
        token_id=token.id, token=refresh_token, exp=exp)
    return response.json(TokenSchema(**{"access_token": access_token, "refresh_token": refresh_token}))
