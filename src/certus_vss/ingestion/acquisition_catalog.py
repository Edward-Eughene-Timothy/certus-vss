"""Broad CARLA acquisition catalog, separate from diagnostic VSS signals.

This module describes observations that may be acquired from CARLA.
It does not connect to CARLA, control vehicles, or authorize diagnostic use.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AcquisitionCategory(str, Enum):
    DYNAMICS = "vehicle_dynamics"
    STATE = "vehicle_state"
    CONTROL_STATE = "vehicle_control_state"
    PHYSICS = "vehicle_physics"
    EVENT = "driving_environment_event"
    ENVIRONMENT = "environment"
    SENSOR = "sensor"


class Provenance(str, Enum):
    CARLA_MEASURED = "CARLA_PROVIDED"
    CARLA_CALCULATED = "CALCULATED_FROM_CARLA"
    CUSTOM_MODEL = "DOCUMENTED_CUSTOM_MODEL"
    NOT_PROVIDED = "NOT_PROVIDED"


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    CONDITIONAL = "CONDITIONAL"


class DataType(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING = "string"
    VECTOR3 = "vector3"
    OBJECT = "object"
    BINARY_REFERENCE = "binary_reference"


@dataclass(frozen=True)
class AcquisitionSignal:
    """Metadata describing one independently acquired observation."""

    identifier: str
    category: AcquisitionCategory
    unit: str
    data_type: DataType
    carla_api: str | None
    provenance: Provenance
    availability: Availability
    description: str


# This catalog is intentionally independent of ring_buffer.SIGNAL_PATHS.
ACQUISITION_CATALOG: tuple[AcquisitionSignal, ...] = (
    # A. Vehicle dynamics
    AcquisitionSignal(
        "vehicle.velocity",
        AcquisitionCategory.DYNAMICS,
        "m/s",
        DataType.VECTOR3,
        "Vehicle.get_velocity",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Vehicle linear velocity in world coordinates.",
    ),
    AcquisitionSignal(
        "vehicle.speed",
        AcquisitionCategory.DYNAMICS,
        "km/h",
        DataType.FLOAT,
        "Vehicle.get_velocity",
        Provenance.CARLA_CALCULATED,
        Availability.AVAILABLE,
        "Magnitude of linear velocity converted to km/h.",
    ),
    AcquisitionSignal(
        "vehicle.acceleration",
        AcquisitionCategory.DYNAMICS,
        "m/s^2",
        DataType.VECTOR3,
        "Vehicle.get_acceleration",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Vehicle acceleration vector.",
    ),
    AcquisitionSignal(
        "vehicle.angular_velocity",
        AcquisitionCategory.DYNAMICS,
        "deg/s",
        DataType.VECTOR3,
        "Vehicle.get_angular_velocity",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Vehicle angular velocity vector.",
    ),

    # B. Vehicle state and simulation metadata
    AcquisitionSignal(
        "vehicle.position",
        AcquisitionCategory.STATE,
        "m",
        DataType.VECTOR3,
        "Vehicle.get_location",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Position within the simulated CARLA environment.",
    ),
    AcquisitionSignal(
        "vehicle.rotation",
        AcquisitionCategory.STATE,
        "degrees",
        DataType.OBJECT,
        "Vehicle.get_transform",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Pitch, yaw and roll in the simulated environment.",
    ),
    AcquisitionSignal(
        "vehicle.simulation_timestamp",
        AcquisitionCategory.STATE,
        "seconds",
        DataType.FLOAT,
        "WorldSnapshot.timestamp.elapsed_seconds",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Simulation elapsed time, distinct from wall-clock UTC.",
    ),
    AcquisitionSignal(
        "vehicle.actor_id",
        AcquisitionCategory.STATE,
        "none",
        DataType.INTEGER,
        "Vehicle.id",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "CARLA simulator actor identifier.",
    ),

    # C. Read-only control-state observations
    AcquisitionSignal(
        "vehicle.control.throttle",
        AcquisitionCategory.CONTROL_STATE,
        "ratio",
        DataType.FLOAT,
        "Vehicle.get_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Observed throttle control value; never commanded by acquisition.",
    ),
    AcquisitionSignal(
        "vehicle.control.brake",
        AcquisitionCategory.CONTROL_STATE,
        "ratio",
        DataType.FLOAT,
        "Vehicle.get_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Observed brake control value.",
    ),
    AcquisitionSignal(
        "vehicle.control.steer",
        AcquisitionCategory.CONTROL_STATE,
        "ratio",
        DataType.FLOAT,
        "Vehicle.get_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Observed steering control value.",
    ),
    AcquisitionSignal(
        "vehicle.control.gear",
        AcquisitionCategory.CONTROL_STATE,
        "gear",
        DataType.INTEGER,
        "Vehicle.get_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Observed simulated gear selection.",
    ),
    AcquisitionSignal(
        "vehicle.control.hand_brake",
        AcquisitionCategory.CONTROL_STATE,
        "boolean",
        DataType.BOOLEAN,
        "Vehicle.get_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Observed handbrake state.",
    ),
    AcquisitionSignal(
        "vehicle.control.reverse",
        AcquisitionCategory.CONTROL_STATE,
        "boolean",
        DataType.BOOLEAN,
        "Vehicle.get_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Observed reverse state.",
    ),

    # D. Vehicle physics
    AcquisitionSignal(
        "vehicle.physics",
        AcquisitionCategory.PHYSICS,
        "object",
        DataType.OBJECT,
        "Vehicle.get_physics_control",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Read-only snapshot of exposed vehicle physics configuration.",
    ),
    AcquisitionSignal(
        "vehicle.wheels",
        AcquisitionCategory.PHYSICS,
        "object",
        DataType.OBJECT,
        "Vehicle.get_physics_control",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Wheel physics attributes exposed by the simulator.",
    ),

    # E. Events and environment
    AcquisitionSignal(
        "event.collision",
        AcquisitionCategory.EVENT,
        "event",
        DataType.OBJECT,
        "sensor.other.collision",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Collision event and available impulse/actor metadata.",
    ),
    AcquisitionSignal(
        "event.lane_invasion",
        AcquisitionCategory.EVENT,
        "event",
        DataType.OBJECT,
        "sensor.other.lane_invasion",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Lane invasion event and crossed marking metadata.",
    ),
    AcquisitionSignal(
        "environment.map",
        AcquisitionCategory.ENVIRONMENT,
        "name",
        DataType.STRING,
        "World.get_map",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Active simulated map identifier.",
    ),
    AcquisitionSignal(
        "environment.weather",
        AcquisitionCategory.ENVIRONMENT,
        "object",
        DataType.OBJECT,
        "World.get_weather",
        Provenance.CARLA_MEASURED,
        Availability.AVAILABLE,
        "Current simulated weather parameters.",
    ),

    # F. Sensor outputs: payloads must use separate storage.
    AcquisitionSignal(
        "sensor.rgb",
        AcquisitionCategory.SENSOR,
        "binary_reference",
        DataType.BINARY_REFERENCE,
        "sensor.camera.rgb",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "RGB image payload reference; not scalar telemetry.",
    ),
    AcquisitionSignal(
        "sensor.depth",
        AcquisitionCategory.SENSOR,
        "binary_reference",
        DataType.BINARY_REFERENCE,
        "sensor.camera.depth",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Depth image payload reference.",
    ),
    AcquisitionSignal(
        "sensor.semantic_segmentation",
        AcquisitionCategory.SENSOR,
        "binary_reference",
        DataType.BINARY_REFERENCE,
        "sensor.camera.semantic_segmentation",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Semantic segmentation image payload reference.",
    ),
    AcquisitionSignal(
        "sensor.lidar",
        AcquisitionCategory.SENSOR,
        "binary_reference",
        DataType.BINARY_REFERENCE,
        "sensor.lidar.ray_cast",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "LiDAR point-cloud payload reference.",
    ),
    AcquisitionSignal(
        "sensor.radar",
        AcquisitionCategory.SENSOR,
        "binary_reference",
        DataType.BINARY_REFERENCE,
        "sensor.other.radar",
        Provenance.CARLA_MEASURED,
        Availability.CONDITIONAL,
        "Radar detection payload reference.",
    ),

    # Standard CARLA does not provide realistic EV measurements.
    AcquisitionSignal(
        "Vehicle.Powertrain.TractionBattery.Temperature",
        AcquisitionCategory.DYNAMICS,
        "celsius",
        DataType.FLOAT,
        None,
        Provenance.NOT_PROVIDED,
        Availability.UNAVAILABLE,
        "Requires a validated custom battery model.",
    ),
    AcquisitionSignal(
        "Vehicle.Powertrain.TractionBattery.Current",
        AcquisitionCategory.DYNAMICS,
        "amperes",
        DataType.FLOAT,
        None,
        Provenance.NOT_PROVIDED,
        Availability.UNAVAILABLE,
        "Requires a validated custom battery model.",
    ),
    AcquisitionSignal(
        "Vehicle.Powertrain.TractionBattery.StateOfCharge",
        AcquisitionCategory.DYNAMICS,
        "percent",
        DataType.FLOAT,
        None,
        Provenance.NOT_PROVIDED,
        Availability.UNAVAILABLE,
        "Requires a validated custom battery model.",
    ),
    AcquisitionSignal(
        "Vehicle.Cabin.HVAC.PowerDraw",
        AcquisitionCategory.DYNAMICS,
        "watts",
        DataType.FLOAT,
        None,
        Provenance.NOT_PROVIDED,
        Availability.UNAVAILABLE,
        "Requires a validated custom HVAC model.",
    ),
    AcquisitionSignal(
        "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
        AcquisitionCategory.DYNAMICS,
        "celsius",
        DataType.FLOAT,
        None,
        Provenance.NOT_PROVIDED,
        Availability.UNAVAILABLE,
        "Requires a validated custom motor thermal model.",
    ),
)


def get_acquisition_signal(identifier: str) -> AcquisitionSignal | None:
    """Return a catalog entry by exact identifier."""
    return next(
        (item for item in ACQUISITION_CATALOG if item.identifier == identifier),
        None,
    )


def get_signals_by_category(
    category: AcquisitionCategory,
) -> tuple[AcquisitionSignal, ...]:
    """Return all acquisition entries belonging to a category."""
    return tuple(
        item for item in ACQUISITION_CATALOG
        if item.category == category
    )


def validate_catalog() -> None:
    """Raise if catalog identifiers are duplicated or metadata is invalid."""
    identifiers = [item.identifier for item in ACQUISITION_CATALOG]

    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate acquisition catalog identifiers")

    for item in ACQUISITION_CATALOG:
        if not item.identifier or not item.unit or not item.description:
            raise ValueError("Acquisition catalog metadata cannot be empty")

        if item.availability == Availability.UNAVAILABLE:
            if item.provenance != Provenance.NOT_PROVIDED:
                raise ValueError(
                    f"Unavailable signal {item.identifier} must use NOT_PROVIDED provenance"
                )


validate_catalog()
