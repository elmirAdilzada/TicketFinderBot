"""
models/trip.py – Dataclasses for ADY trip data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TripDate:
    """A single available departure date returned by get_trip_dates / get_trip."""
    trip_date_val: str          # "2026-06-26"  (YYYY-MM-DD)
    trip_date_txt: str          # "26-06-2026"  (DD-MM-YYYY, as shown on site)
    min_amount: float           # Minimum ticket price
    min_coefficient: float      # Dynamic pricing coefficient


@dataclass
class WagonClass:
    """One wagon/seat class within a train trip."""
    wagon_type: str             # e.g. "Luxe", "Coupe", "Platz"
    seat_class: str             # e.g. "L", "K", "P"
    seat_class_id: int
    price_adult_lower: float    # Lower berth price
    price_adult_upper: float    # Upper berth price (0 for Luxe)
    total_free_seats: int
    wagon_ids: list[int] = field(default_factory=list)

    @property
    def display_price(self) -> str:
        if self.price_adult_upper:
            return f"{self.price_adult_lower:.2f} / {self.price_adult_upper:.2f} AZN"
        return f"{self.price_adult_lower:.2f} AZN"


@dataclass
class Trip:
    """Full trip details returned by get_traintrip."""
    trip_id: int
    train_number: str
    train_type: str
    route_name: str
    depart_datetime: str        # "26-07-2026 23:10:00"
    arrival_datetime: str       # "27-07-2026 08:41:00"
    last_sale_time: str
    wagon_classes: list[WagonClass] = field(default_factory=list)

    @property
    def depart_time(self) -> str:
        """Extract HH:MM from depart_datetime."""
        try:
            return self.depart_datetime.split(" ")[1][:5]
        except (IndexError, AttributeError):
            return self.depart_datetime

    @property
    def arrival_time(self) -> str:
        """Extract HH:MM from arrival_datetime."""
        try:
            return self.arrival_datetime.split(" ")[1][:5]
        except (IndexError, AttributeError):
            return self.arrival_datetime

    @property
    def depart_date(self) -> str:
        """Extract DD-MM-YYYY from depart_datetime."""
        try:
            return self.depart_datetime.split(" ")[0]
        except (IndexError, AttributeError):
            return self.depart_datetime

    @property
    def total_free_seats(self) -> int:
        return sum(wc.total_free_seats for wc in self.wagon_classes)

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON state persistence."""
        return {
            "trip_id": self.trip_id,
            "train_number": self.train_number,
            "train_type": self.train_type,
            "route_name": self.route_name,
            "depart_datetime": self.depart_datetime,
            "arrival_datetime": self.arrival_datetime,
            "last_sale_time": self.last_sale_time,
            "wagon_classes": [
                {
                    "wagon_type": wc.wagon_type,
                    "seat_class": wc.seat_class,
                    "seat_class_id": wc.seat_class_id,
                    "price_adult_lower": wc.price_adult_lower,
                    "price_adult_upper": wc.price_adult_upper,
                    "total_free_seats": wc.total_free_seats,
                    "wagon_ids": wc.wagon_ids,
                }
                for wc in self.wagon_classes
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trip":
        wagon_classes = [
            WagonClass(
                wagon_type=wc["wagon_type"],
                seat_class=wc["seat_class"],
                seat_class_id=wc["seat_class_id"],
                price_adult_lower=wc["price_adult_lower"],
                price_adult_upper=wc["price_adult_upper"],
                total_free_seats=wc["total_free_seats"],
                wagon_ids=wc.get("wagon_ids", []),
            )
            for wc in d.get("wagon_classes", [])
        ]
        return cls(
            trip_id=d["trip_id"],
            train_number=d["train_number"],
            train_type=d.get("train_type", ""),
            route_name=d.get("route_name", ""),
            depart_datetime=d["depart_datetime"],
            arrival_datetime=d["arrival_datetime"],
            last_sale_time=d.get("last_sale_time", ""),
            wagon_classes=wagon_classes,
        )


@dataclass
class RouteSnapshot:
    """
    Complete snapshot of one route at a point in time.
    Keyed by trip_date_val → Optional[Trip].
    None means the date is listed but get_traintrip returned no seats.
    """
    label: str                                  # "Baku → Tbilisi"
    from_station: int
    to_station: int
    # date string → serialised Trip dict (or None)
    trips: dict[str, Optional[dict]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "from_station": self.from_station,
            "to_station": self.to_station,
            "trips": self.trips,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RouteSnapshot":
        return cls(
            label=d["label"],
            from_station=d["from_station"],
            to_station=d["to_station"],
            trips=d.get("trips", {}),
        )
