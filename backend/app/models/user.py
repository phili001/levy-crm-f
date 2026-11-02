import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    cliente = "cliente"
    # Cuenta de solo mirar para quien revisa la app en Meta: ve la bandeja de
    # Instagram y la ficha de la cuenta conectada, y nada más. Lo que puede
    # tocar está en la lista blanca de api/deps.py, no aquí.
    revisor = "revisor"


user_role_enum = ENUM(UserRole, name="user_role", create_type=False, values_callable=lambda x: [e.value for e in x])


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(user_role_enum, nullable=False)
    nombre: Mapped[str | None] = mapped_column(String(120))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Última vez que el usuario entró (login OK por contraseña o Google). NULL si
    # nunca ha entrado. Se actualiza en cada login correcto.
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sello del último cambio de contraseña: los JWT con `iat` anterior se
    # rechazan (un token robado muere al cambiar la clave, no a su exp).
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # La pone el sembrado en el administrador inicial: su contraseña viene de
    # las variables de entorno y la ha visto cualquiera con acceso al panel de
    # despliegue. Mientras esté a True, la sesión solo sirve para cambiarla
    # (api/deps.py). Se apaga al hacerlo.
    debe_cambiar_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
