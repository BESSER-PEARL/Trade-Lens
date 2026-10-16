from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from pydantic_classes import *
from sql_alchemy import *
from database import get_db
from bal_stdlib import *

router = APIRouter()


@router.get("/observation/", response_model=None, tags=["Observation"])
def get_all_observation(detailed: bool = False, database: Session = Depends(get_db)) -> list:
    # Use detailed=true to get entities with eagerly loaded relationships (for tables with lookup columns)
    if detailed:
        # Eagerly load all relationships to avoid N+1 queries
        query = database.query(Observation)
        query = query.options(joinedload(Observation.partner))
        query = query.options(joinedload(Observation.product))
        query = query.options(joinedload(Observation.reporter))
        observation_list = query.all()

        # Serialize with relationships included
        result = []
        for observation_item in observation_list:
            item_dict = observation_item.__dict__.copy()
            item_dict.pop('_sa_instance_state', None)

            # Add many-to-one relationships (foreign keys for lookup columns)
            if observation_item.partner:
                related_obj = observation_item.partner
                related_dict = related_obj.__dict__.copy()
                related_dict.pop('_sa_instance_state', None)
                item_dict['partner'] = related_dict
            else:
                item_dict['partner'] = None
            if observation_item.product:
                related_obj = observation_item.product
                related_dict = related_obj.__dict__.copy()
                related_dict.pop('_sa_instance_state', None)
                item_dict['product'] = related_dict
            else:
                item_dict['product'] = None
            if observation_item.reporter:
                related_obj = observation_item.reporter
                related_dict = related_obj.__dict__.copy()
                related_dict.pop('_sa_instance_state', None)
                item_dict['reporter'] = related_dict
            else:
                item_dict['reporter'] = None


            result.append(item_dict)
        return result
    else:
        # Default: return flat entities (faster for charts/widgets without lookup columns)
        return database.query(Observation).all()


@router.get("/observation/count/", response_model=None, tags=["Observation"])
def get_count_observation(database: Session = Depends(get_db)) -> dict:
    """Get the total count of Observation entities"""
    count = database.query(Observation).count()
    return {"count": count}


@router.get("/observation/paginated/", response_model=None, tags=["Observation"])
def get_paginated_observation(skip: int = 0, limit: int = 100, detailed: bool = False, database: Session = Depends(get_db)) -> dict:
    """Get paginated list of Observation entities"""
    total = database.query(Observation).count()
    observation_list = database.query(Observation).offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": observation_list
    }
@router.get("/observation/search/", response_model=None, tags=["Observation"])
def search_observation(
    obs_value: float = None,
    time_period: str = None,
    database: Session = Depends(get_db)
) -> list:
    """Search Observation entities by attributes"""
    query = database.query(Observation)

    if obs_value is not None:
        query = query.filter(Observation.obs_value == obs_value)
    if time_period is not None:
        query = query.filter(Observation.time_period.ilike(f"%{time_period}%"))

    results = query.all()
    return results


@router.get("/observation/{observation_id}/", response_model=None, tags=["Observation"])
async def get_observation(observation_id: int, database: Session = Depends(get_db)) -> Observation:
    db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
    if db_observation is None:
        raise HTTPException(status_code=404, detail="Observation not found")

    response_data = {
        "observation": db_observation,
}
    return response_data



@router.post("/observation/", response_model=None, tags=["Observation"])
async def create_observation(observation_data: ObservationCreate, database: Session = Depends(get_db)) -> Observation:

    if observation_data.partner is not None:
        db_partner = database.query(Country).filter(Country.code == observation_data.partner).first()
        if not db_partner:
            raise HTTPException(status_code=400, detail="Country not found")
    else:
        raise HTTPException(status_code=400, detail="Country ID is required")
    if observation_data.product is not None:
        db_product = database.query(Product).filter(Product.code == observation_data.product).first()
        if not db_product:
            raise HTTPException(status_code=400, detail="Product not found")
    else:
        raise HTTPException(status_code=400, detail="Product ID is required")
    if observation_data.reporter is not None:
        db_reporter = database.query(Country).filter(Country.code == observation_data.reporter).first()
        if not db_reporter:
            raise HTTPException(status_code=400, detail="Country not found")
    else:
        raise HTTPException(status_code=400, detail="Country ID is required")

    db_observation = Observation(
        flow=observation_data.flow.name,        freq=observation_data.freq.name,        indicators=observation_data.indicators.name,        obs_value=observation_data.obs_value,        time_period=observation_data.time_period,        partner_id=observation_data.partner,        product_id=observation_data.product,        reporter_id=observation_data.reporter        )

    database.add(db_observation)
    database.flush()
    database.refresh(db_observation)




    database.commit()
    database.refresh(db_observation)
    return db_observation


@router.post("/observation/bulk/", response_model=None, tags=["Observation"])
async def bulk_create_observation(items: list[ObservationCreate], database: Session = Depends(get_db)) -> dict:
    """Create multiple Observation entities at once"""
    created_items = []
    errors = []

    for idx, item_data in enumerate(items):
        try:
            # Basic validation for each item
            if not item_data.partner:
                raise ValueError("Country ID is required")
            if not item_data.product:
                raise ValueError("Product ID is required")
            if not item_data.reporter:
                raise ValueError("Country ID is required")

            db_observation = Observation(
                flow=item_data.flow.name,                freq=item_data.freq.name,                indicators=item_data.indicators.name,                obs_value=item_data.obs_value,                time_period=item_data.time_period,                partner_id=item_data.partner,                product_id=item_data.product,                reporter_id=item_data.reporter            )
            database.add(db_observation)
            database.flush()  # Get ID without committing
            created_items.append(db_observation.id)
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})

    if errors:
        database.rollback()
        raise HTTPException(status_code=400, detail={"message": "Bulk creation failed", "errors": errors})

    database.commit()
    return {
        "created_count": len(created_items),
        "created_ids": created_items,
        "message": f"Successfully created {len(created_items)} Observation entities"
    }


@router.delete("/observation/bulk/", response_model=None, tags=["Observation"])
async def bulk_delete_observation(ids: list[int], database: Session = Depends(get_db)) -> dict:
    """Delete multiple Observation entities at once"""
    deleted_count = 0
    not_found = []

    for item_id in ids:
        db_observation = database.query(Observation).filter(Observation.id == item_id).first()
        if db_observation:
            database.delete(db_observation)
            deleted_count += 1
        else:
            not_found.append(item_id)

    database.commit()

    return {
        "deleted_count": deleted_count,
        "not_found": not_found,
        "message": f"Successfully deleted {deleted_count} Observation entities"
    }

@router.put("/observation/{observation_id}/", response_model=None, tags=["Observation"])
async def update_observation(observation_id: int, observation_data: ObservationCreate, database: Session = Depends(get_db)) -> Observation:
    db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
    if db_observation is None:
        raise HTTPException(status_code=404, detail="Observation not found")

    setattr(db_observation, 'flow', observation_data.flow.name)
    setattr(db_observation, 'freq', observation_data.freq.name)
    setattr(db_observation, 'indicators', observation_data.indicators.name)
    setattr(db_observation, 'obs_value', observation_data.obs_value)
    setattr(db_observation, 'time_period', observation_data.time_period)
    if observation_data.partner is not None:
        db_partner = database.query(Country).filter(Country.code == observation_data.partner).first()
        if not db_partner:
            raise HTTPException(status_code=400, detail="Country not found")
        setattr(db_observation, 'partner_id', observation_data.partner)
    if observation_data.product is not None:
        db_product = database.query(Product).filter(Product.code == observation_data.product).first()
        if not db_product:
            raise HTTPException(status_code=400, detail="Product not found")
        setattr(db_observation, 'product_id', observation_data.product)
    if observation_data.reporter is not None:
        db_reporter = database.query(Country).filter(Country.code == observation_data.reporter).first()
        if not db_reporter:
            raise HTTPException(status_code=400, detail="Country not found")
        setattr(db_observation, 'reporter_id', observation_data.reporter)
    database.commit()
    database.refresh(db_observation)

    return db_observation


@router.delete("/observation/{observation_id}/", response_model=None, tags=["Observation"])
async def delete_observation(observation_id: int, database: Session = Depends(get_db)):
    db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
    if db_observation is None:
        raise HTTPException(status_code=404, detail="Observation not found")
    # Snapshot the columns before deleting: cascading the association-class
    # links loads relationship collections whose back-references would make
    # the JSON encoder recurse endlessly on the live object.
    deleted_observation = {
        attr.key: getattr(db_observation, attr.key)
        for attr in db_observation.__mapper__.column_attrs
    }
    database.delete(db_observation)
    database.commit()
    return deleted_observation



