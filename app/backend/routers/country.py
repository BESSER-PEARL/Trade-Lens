from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from pydantic_classes import *
from sql_alchemy import *
from database import get_db
from bal_stdlib import *

router = APIRouter()


@router.get("/country/", response_model=None, tags=["Country"])
def get_all_country(detailed: bool = False, database: Session = Depends(get_db)) -> list:
    # Use detailed=true to get entities with eagerly loaded relationships (for tables with lookup columns)
    if detailed:
        # Eagerly load all relationships to avoid N+1 queries
        query = database.query(Country)
        country_list = query.all()

        # Serialize with relationships included
        result = []
        for country_item in country_list:
            item_dict = country_item.__dict__.copy()
            item_dict.pop('_sa_instance_state', None)

            # Add many-to-one relationships (foreign keys for lookup columns)

            # Add many-to-many and one-to-many relationship objects (full details)
            observation_list = database.query(Observation).filter(Observation.partner_id == country_item.code).all()
            item_dict['observations_as_partner'] = []
            for observation_obj in observation_list:
                observation_dict = observation_obj.__dict__.copy()
                observation_dict.pop('_sa_instance_state', None)
                item_dict['observations_as_partner'].append(observation_dict)
            observation_list = database.query(Observation).filter(Observation.reporter_id == country_item.code).all()
            item_dict['observations_as_reporter'] = []
            for observation_obj in observation_list:
                observation_dict = observation_obj.__dict__.copy()
                observation_dict.pop('_sa_instance_state', None)
                item_dict['observations_as_reporter'].append(observation_dict)

            result.append(item_dict)
        return result
    else:
        # Default: return flat entities (faster for charts/widgets without lookup columns)
        return database.query(Country).all()


@router.get("/country/count/", response_model=None, tags=["Country"])
def get_count_country(database: Session = Depends(get_db)) -> dict:
    """Get the total count of Country entities"""
    count = database.query(Country).count()
    return {"count": count}


@router.get("/country/paginated/", response_model=None, tags=["Country"])
def get_paginated_country(skip: int = 0, limit: int = 100, detailed: bool = False, database: Session = Depends(get_db)) -> dict:
    """Get paginated list of Country entities"""
    total = database.query(Country).count()
    country_list = database.query(Country).offset(skip).limit(limit).all()
    # By default, return flat entities (for charts/widgets)
    # Use detailed=true to get entities with relationships
    if not detailed:
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "data": country_list
        }

    result = []
    for country_item in country_list:
        observations_as_partner_ids = database.query(Observation.id).filter(Observation.partner_id == country_item.code).all()
        observations_as_reporter_ids = database.query(Observation.id).filter(Observation.reporter_id == country_item.code).all()
        item_data = {
            "country": country_item,
            "observations_as_partner_ids": [x[0] for x in observations_as_partner_ids],            "observations_as_reporter_ids": [x[0] for x in observations_as_reporter_ids]        }
        result.append(item_data)
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": result
    }
@router.get("/country/search/", response_model=None, tags=["Country"])
def search_country(
    code: str = None,
    label: str = None,
    database: Session = Depends(get_db)
) -> list:
    """Search Country entities by attributes"""
    query = database.query(Country)

    if code is not None:
        query = query.filter(Country.code.ilike(f"%{code}%"))
    if label is not None:
        query = query.filter(Country.label.ilike(f"%{label}%"))

    results = query.all()
    return results


@router.get("/country/{country_id}/", response_model=None, tags=["Country"])
async def get_country(country_id: str, database: Session = Depends(get_db)) -> Country:
    db_country = database.query(Country).filter(Country.code == country_id).first()
    if db_country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    observations_as_partner_ids = database.query(Observation.id).filter(Observation.partner_id == db_country.code).all()
    observations_as_reporter_ids = database.query(Observation.id).filter(Observation.reporter_id == db_country.code).all()
    response_data = {
        "country": db_country,
        "observations_as_partner_ids": [x[0] for x in observations_as_partner_ids],        "observations_as_reporter_ids": [x[0] for x in observations_as_reporter_ids]}
    return response_data



@router.post("/country/", response_model=None, tags=["Country"])
async def create_country(country_data: CountryCreate, database: Session = Depends(get_db)) -> Country:


    db_country = Country(
        code=country_data.code,        label=country_data.label        )

    database.add(db_country)
    database.flush()
    database.refresh(db_country)

    if country_data.observations_as_partner:
        # Validate that all Observation IDs exist
        for observation_id in country_data.observations_as_partner:
            db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
            if not db_observation:
                raise HTTPException(status_code=400, detail=f"Observation with id {observation_id} not found")

        # Update the related entities with the new foreign key
        database.query(Observation).filter(Observation.id.in_(country_data.observations_as_partner)).update(
            {Observation.partner_id: db_country.code}, synchronize_session=False
        )
        database.flush()
    if country_data.observations_as_reporter:
        # Validate that all Observation IDs exist
        for observation_id in country_data.observations_as_reporter:
            db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
            if not db_observation:
                raise HTTPException(status_code=400, detail=f"Observation with id {observation_id} not found")

        # Update the related entities with the new foreign key
        database.query(Observation).filter(Observation.id.in_(country_data.observations_as_reporter)).update(
            {Observation.reporter_id: db_country.code}, synchronize_session=False
        )
        database.flush()



    observations_as_partner_ids = database.query(Observation.id).filter(Observation.partner_id == db_country.code).all()
    observations_as_reporter_ids = database.query(Observation.id).filter(Observation.reporter_id == db_country.code).all()
    response_data = {
        "country": db_country,
        "observations_as_partner_ids": [x[0] for x in observations_as_partner_ids],        "observations_as_reporter_ids": [x[0] for x in observations_as_reporter_ids]    }
    database.commit()
    database.refresh(db_country)
    return response_data


@router.post("/country/bulk/", response_model=None, tags=["Country"])
async def bulk_create_country(items: list[CountryCreate], database: Session = Depends(get_db)) -> dict:
    """Create multiple Country entities at once"""
    if any(getattr(item, field, None) for item in items for field in ['observations_as_partner', 'observations_as_reporter']):
        raise HTTPException(status_code=422, detail="Bulk creation of Country with relationships is not supported; use POST /country/ to validate and persist its relationships")
    created_items = []
    errors = []

    for idx, item_data in enumerate(items):
        try:
            # Basic validation for each item

            db_country = Country(
                code=item_data.code,                label=item_data.label            )
            database.add(db_country)
            database.flush()  # Get ID without committing
            created_items.append(db_country.code)
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})

    if errors:
        database.rollback()
        raise HTTPException(status_code=400, detail={"message": "Bulk creation failed", "errors": errors})

    database.commit()
    return {
        "created_count": len(created_items),
        "created_ids": created_items,
        "message": f"Successfully created {len(created_items)} Country entities"
    }


@router.delete("/country/bulk/", response_model=None, tags=["Country"])
async def bulk_delete_country(ids: list[str], database: Session = Depends(get_db)) -> dict:
    """Delete multiple Country entities at once"""
    # Aggregate deletion must account for every related lower bound. Until it
    # shares that validation, use the guarded individual delete endpoint.
    if ids:
        raise HTTPException(status_code=409, detail="Bulk deletion of Country can violate required relationships and is not supported; use the individual DELETE /country/ endpoint")
    deleted_count = 0
    not_found = []

    for item_id in ids:
        db_country = database.query(Country).filter(Country.code == item_id).first()
        if db_country:
            database.delete(db_country)
            deleted_count += 1
        else:
            not_found.append(item_id)

    database.commit()

    return {
        "deleted_count": deleted_count,
        "not_found": not_found,
        "message": f"Successfully deleted {deleted_count} Country entities"
    }

@router.put("/country/{country_id}/", response_model=None, tags=["Country"])
async def update_country(country_id: str, country_data: CountryCreate, database: Session = Depends(get_db)) -> Country:
    db_country = database.query(Country).filter(Country.code == country_id).first()
    if db_country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    setattr(db_country, 'label', country_data.label)
    if country_data.observations_as_partner is not None:
        requested_observations_as_partner = set(country_data.observations_as_partner)
        current_observations_as_partner = {
            getattr(item, 'id')
            for item in database.query(Observation).filter(Observation.partner_id == db_country.code).all()
        }
        observations_as_partner_to_detach = current_observations_as_partner - requested_observations_as_partner
        if observations_as_partner_to_detach:
            raise HTTPException(status_code=409, detail="Observation " + ", ".join(str(item) for item in sorted(observations_as_partner_to_detach)) + " requires a partner: reassign it instead of removing it")
        observations_as_partner_to_attach = requested_observations_as_partner - current_observations_as_partner
        for observation_id in observations_as_partner_to_attach:
            db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
            if not db_observation:
                raise HTTPException(status_code=400, detail="Observation with id " + str(observation_id) + " not found")
        if observations_as_partner_to_attach:
            database.query(Observation).filter(Observation.id.in_(observations_as_partner_to_attach)).update(
                {Observation.partner_id: db_country.code}, synchronize_session=False
            )
    if country_data.observations_as_reporter is not None:
        requested_observations_as_reporter = set(country_data.observations_as_reporter)
        current_observations_as_reporter = {
            getattr(item, 'id')
            for item in database.query(Observation).filter(Observation.reporter_id == db_country.code).all()
        }
        observations_as_reporter_to_detach = current_observations_as_reporter - requested_observations_as_reporter
        if observations_as_reporter_to_detach:
            raise HTTPException(status_code=409, detail="Observation " + ", ".join(str(item) for item in sorted(observations_as_reporter_to_detach)) + " requires a reporter: reassign it instead of removing it")
        observations_as_reporter_to_attach = requested_observations_as_reporter - current_observations_as_reporter
        for observation_id in observations_as_reporter_to_attach:
            db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
            if not db_observation:
                raise HTTPException(status_code=400, detail="Observation with id " + str(observation_id) + " not found")
        if observations_as_reporter_to_attach:
            database.query(Observation).filter(Observation.id.in_(observations_as_reporter_to_attach)).update(
                {Observation.reporter_id: db_country.code}, synchronize_session=False
            )
    database.commit()
    database.refresh(db_country)

    observations_as_partner_ids = database.query(Observation.id).filter(Observation.partner_id == db_country.code).all()
    observations_as_reporter_ids = database.query(Observation.id).filter(Observation.reporter_id == db_country.code).all()
    response_data = {
        "country": db_country,
        "observations_as_partner_ids": [x[0] for x in observations_as_partner_ids],        "observations_as_reporter_ids": [x[0] for x in observations_as_reporter_ids]    }
    return response_data


@router.delete("/country/{country_id}/", response_model=None, tags=["Country"])
async def delete_country(country_id: str, database: Session = Depends(get_db)):
    db_country = database.query(Country).filter(Country.code == country_id).first()
    if db_country is None:
        raise HTTPException(status_code=404, detail="Country not found")
    related_observations_as_partner = db_country.observations_as_partner
    for related in (list(related_observations_as_partner) if isinstance(related_observations_as_partner, list) else [item for item in [related_observations_as_partner] if item is not None]):
        remaining = 0
        if remaining < 1:
            raise HTTPException(status_code=409, detail="Cannot delete Country " + str(country_id) + ": Observation " + str(getattr(related, 'id')) + " requires at least 1 partner")
    related_observations_as_reporter = db_country.observations_as_reporter
    for related in (list(related_observations_as_reporter) if isinstance(related_observations_as_reporter, list) else [item for item in [related_observations_as_reporter] if item is not None]):
        remaining = 0
        if remaining < 1:
            raise HTTPException(status_code=409, detail="Cannot delete Country " + str(country_id) + ": Observation " + str(getattr(related, 'id')) + " requires at least 1 reporter")
    # Snapshot the columns before deleting: cascading the association-class
    # links loads relationship collections whose back-references would make
    # the JSON encoder recurse endlessly on the live object.
    deleted_country = {
        attr.key: getattr(db_country, attr.key)
        for attr in db_country.__mapper__.column_attrs
    }
    database.delete(db_country)
    database.commit()
    return deleted_country


@router.get("/country/{country_id}/observations_as_partner/", response_model=None, tags=["Country Relationships"])
async def get_observations_as_partner_of_country(country_id: str, database: Session = Depends(get_db)):
    """Get all Observation entities related to this Country through observations_as_partner"""
    db_country = database.query(Country).filter(Country.code == country_id).first()
    if db_country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    observations_as_partner_list = database.query(Observation).filter(Observation.partner_id == country_id).all()

    return {
        "country_id": country_id,
        "observations_as_partner_count": len(observations_as_partner_list),
        "observations_as_partner": observations_as_partner_list
    }

@router.get("/country/{country_id}/observations_as_reporter/", response_model=None, tags=["Country Relationships"])
async def get_observations_as_reporter_of_country(country_id: str, database: Session = Depends(get_db)):
    """Get all Observation entities related to this Country through observations_as_reporter"""
    db_country = database.query(Country).filter(Country.code == country_id).first()
    if db_country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    observations_as_reporter_list = database.query(Observation).filter(Observation.reporter_id == country_id).all()

    return {
        "country_id": country_id,
        "observations_as_reporter_count": len(observations_as_reporter_list),
        "observations_as_reporter": observations_as_reporter_list
    }


