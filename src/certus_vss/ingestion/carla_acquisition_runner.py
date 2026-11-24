

"""Dynamic, read-only CARLA broad-acquisition runner."""



from __future__ import annotations



import argparse

import logging

import math

import time

import statistics

from certus_vss.ingestion.carla_acquisition import (

    collect_carla_observations,

)

from certus_vss.storage.acquisition_store import (

    save_acquisition_samples,

)



from typing import Any



from certus_vss.storage.acquisition_store import (

    initialize_acquisition_storage,

)



LOGGER = logging.getLogger("carla_acquisition_runner")





def discover_vehicles(world: Any) -> list[Any]:

    """Return currently available vehicle actors without spawning anything."""

    actors = world.get_actors()

    vehicles = list(actors.filter("vehicle.*"))

    return sorted(vehicles, key=lambda actor: int(actor.id))





def describe_vehicle(vehicle: Any) -> str:

    """Create a concise description of a discovered vehicle."""

    actor_id = int(vehicle.id)

    type_id = str(getattr(vehicle, "type_id", "unknown"))



    try:

        location = vehicle.get_transform().location

        position = (

            f"x={float(location.x):.2f}, "

            f"y={float(location.y):.2f}, "

            f"z={float(location.z):.2f}"

        )

    except (AttributeError, RuntimeError, TypeError, ValueError):

        position = "position unavailable"



    return f"Actor {actor_id}: {type_id} ({position})"





def select_vehicle(

    world: Any,

    actor_id: int | None = None,

) -> Any:

    """Discover vehicles and select one explicitly when necessary."""

    vehicles = discover_vehicles(world)



    if not vehicles:

        raise RuntimeError(

            "No vehicles found in CARLA. "

            "No vehicle will be spawned automatically."

        )



    print("\nDiscovered CARLA vehicles:")

    for index, vehicle in enumerate(vehicles, start=1):

        print(f"  {index}. {describe_vehicle(vehicle)}")



    if actor_id is not None:

        selected = next(

            (vehicle for vehicle in vehicles if int(vehicle.id) == actor_id),

            None,

        )

        if selected is None:

            raise RuntimeError(

                f"Vehicle actor ID {actor_id} was not found."

            )

        return selected



    if len(vehicles) == 1:

        selected = vehicles[0]

        print(f"\nOnly one vehicle found. Selecting actor {selected.id}.")

        return selected



    while True:

        try:

            choice = input(

                "\nSelect vehicle by list number: "

            ).strip()

            index = int(choice) - 1

        except EOFError:

            raise RuntimeError(

                "Vehicle selection cancelled: no interactive input available."

            ) from None

        except ValueError:

            print("Enter a valid vehicle number.")

            continue



        if 0 <= index < len(vehicles):

            return vehicles[index]



        print("Selection is outside the displayed list.")



def percentile(values: list[float], percent: float) -> float:

    """Return a percentile using nearest-rank calculation."""

    if not values:

        return 0.0



    ordered = sorted(values)

    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)

    return ordered[index]





def print_timing_summary(

    collection_ms: list[float],

    persistence_ms: list[float],

    cycle_ms: list[float],

    elapsed_s: float,

) -> None:

    """Print acquisition timing statistics."""

    def report(name: str, values: list[float]) -> None:

        print(

            f"{name:<18} "

            f"median={statistics.median(values):.3f} ms "

            f"p95={percentile(values, 95):.3f} ms"

        )



    print("\n\nCARLA acquisition timing")

    print("-" * 48)

    report("Collection", collection_ms)

    report("Persistence", persistence_ms)

    report("Total cycle", cycle_ms)

    print(f"Effective rate: {len(cycle_ms) / elapsed_s:.2f} Hz")

    print(f"Elapsed time:   {elapsed_s:.3f} s")





def run_acquisition(

    client: Any,

    vehicle: Any,

    *,

    sample_hz: float = 5.0,

    duration_s: float | None = None,

) -> bool:

    """Collect and persist observations until stopped or a fatal error occurs."""

    if not math.isfinite(sample_hz) or sample_hz <= 0:

        raise ValueError("sample_hz must be positive and finite")



    if duration_s is not None:

        if not math.isfinite(duration_s) or duration_s <= 0:

            raise ValueError("duration_s must be positive and finite")



    start_time = time.monotonic()

    deadline = start_time

    actor_id = int(vehicle.id)



    total_records = 0

    cycles = 0

    success = True

    collection_ms: list[float] = []

    persistence_ms: list[float] = []

    cycle_ms: list[float] = []



    print(f"\nStarting read-only acquisition for actor {actor_id}.")

    print(f"Sampling rate: {sample_hz:g} Hz")

    print("Press Ctrl+C to stop.\n")



    while duration_s is None or (

        time.monotonic() - start_time < duration_s

    ):

        cycle_start = time.perf_counter()



        try:

            world = client.get_world()

            current_vehicle = world.get_actor(actor_id)



            if current_vehicle is None:

                LOGGER.error(

                    "Vehicle actor %s is no longer available. Stopping.",

                    actor_id,

                )

                success = False

                break



            snapshot = world.get_snapshot()



            collection_start = time.perf_counter()

            records, metadata = collect_carla_observations(

                world,

                current_vehicle,

                snapshot=snapshot,

            )

            collection_duration = (

                time.perf_counter() - collection_start

            ) * 1000



            persistence_start = time.perf_counter()

            inserted_ids = save_acquisition_samples(

                records,

                simulation_time_s=metadata["simulation_time_s"],

                actor_id=metadata["actor_id"],

            )

            persistence_duration = (

                time.perf_counter() - persistence_start

            ) * 1000



        except Exception:

            LOGGER.exception(

                "Acquisition cycle failed for actor %s. Stopping safely.",

                actor_id,

            )

            success = False

            break



        cycle_duration = (

            time.perf_counter() - cycle_start

        ) * 1000



        collection_ms.append(collection_duration)

        persistence_ms.append(persistence_duration)

        cycle_ms.append(cycle_duration)



        cycles += 1

        total_records += len(inserted_ids)



        print(

            f"\rCycles: {cycles} | "

            f"Records: {total_records} | "

            f"Last cycle: {cycle_duration:.2f} ms",

            end="",

            flush=True,

        )



        deadline += 1.0 / sample_hz

        delay = deadline - time.monotonic()



        if delay > 0:

            time.sleep(delay)

        else:

            deadline = time.monotonic()



    elapsed_s = time.monotonic() - start_time



    print(

        f"\n\nAcquisition stopped. "

        f"Cycles: {cycles}; records persisted: {total_records}."

    )



    if cycles:

        print_timing_summary(

            collection_ms,

            persistence_ms,

            cycle_ms,

            elapsed_s,

        )



    return success



def main() -> int:

    parser = argparse.ArgumentParser(

        description="Read-only CARLA broad-acquisition runner."

    )

    parser.add_argument("--host", default="127.0.0.1")

    parser.add_argument("--port", type=int, default=2000)

    parser.add_argument("--timeout", type=float, default=5.0)

    parser.add_argument("--hz", type=float, default=5.0)

    parser.add_argument(

        "--duration",

        type=float,

        default=None,

        help="Stop acquisition after this many seconds.",

    )

    parser.add_argument(

        "--actor-id",

        type=int,

        default=None,

        help="Explicit vehicle actor ID. Omit to discover interactively.",

    )

    parser.add_argument(

        "--list",

        action="store_true",

        help="List discovered vehicles and exit.",

    )

    args = parser.parse_args()



    if not math.isfinite(args.hz) or args.hz <= 0:

        parser.error("--hz must be positive and finite")



    if args.duration is not None and (

        not math.isfinite(args.duration) or args.duration <= 0

    ):

        parser.error("--duration must be positive and finite")



    if args.port < 1 or args.port > 65535:

        parser.error("--port must be between 1 and 65535")



    try:

        import carla

    except ImportError:

        LOGGER.error(

            "CARLA Python API is unavailable. Activate the CARLA venv."

        )

        return 1



    try:

        client = carla.Client(args.host, args.port)

        client.set_timeout(args.timeout)



        world = client.get_world()



        if args.list:

            vehicles = discover_vehicles(world)

            if not vehicles:

                print("No vehicles found.")

            else:

                print("\nDiscovered CARLA vehicles:")

                for vehicle in vehicles:

                    print(f"  {describe_vehicle(vehicle)}")

            return 0



        vehicle = select_vehicle(world, args.actor_id)



        # Initialize schema once, before entering the polling loop.

        initialize_acquisition_storage()



        success = run_acquisition(

            client,

            vehicle,

            sample_hz=args.hz,

            duration_s=args.duration,

        )



        if not success:

            return 1



    except KeyboardInterrupt:

        print("\nInterrupted by user.")

    except Exception as exc:

        LOGGER.error("Runner failed: %s", exc)

        return 1



    return 0





if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)

    raise SystemExit(main())
