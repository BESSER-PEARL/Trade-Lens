from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload
from pydantic_classes import *
from sql_alchemy import *
from database import get_db
from bal_stdlib import *

router = APIRouter()


@router.get("/product/", response_model=None, tags=["Product"])
def get_all_product(detailed: bool = False, database: Session = Depends(get_db)) -> list:
    # Use detailed=true to get entities with eagerly loaded relationships (for tables with lookup columns)
    if detailed:
        # Eagerly load all relationships to avoid N+1 queries
        query = database.query(Product)
        product_list = query.all()

        # Serialize with relationships included
        result = []
        for product_item in product_list:
            item_dict = product_item.__dict__.copy()
            item_dict.pop('_sa_instance_state', None)

            # Add many-to-one relationships (foreign keys for lookup columns)

            # Add many-to-many and one-to-many relationship objects (full details)
            observation_list = database.query(Observation).filter(Observation.product_id == product_item.code).all()
            item_dict['observations'] = []
            for observation_obj in observation_list:
                observation_dict = observation_obj.__dict__.copy()
                observation_dict.pop('_sa_instance_state', None)
                item_dict['observations'].append(observation_dict)
            product_list = database.query(Product).filter(Product.parent_id == product_item.code).all()
            item_dict['children'] = []
            for product_obj in product_list:
                product_dict = product_obj.__dict__.copy()
                product_dict.pop('_sa_instance_state', None)
                item_dict['children'].append(product_dict)

            result.append(item_dict)
        return result
    else:
        # Default: return flat entities (faster for charts/widgets without lookup columns)
        return database.query(Product).all()


@router.get("/product/count/", response_model=None, tags=["Product"])
def get_count_product(database: Session = Depends(get_db)) -> dict:
    """Get the total count of Product entities"""
    count = database.query(Product).count()
    return {"count": count}


@router.get("/product/paginated/", response_model=None, tags=["Product"])
def get_paginated_product(skip: int = 0, limit: int = 100, detailed: bool = False, database: Session = Depends(get_db)) -> dict:
    """Get paginated list of Product entities"""
    total = database.query(Product).count()
    product_list = database.query(Product).offset(skip).limit(limit).all()
    # By default, return flat entities (for charts/widgets)
    # Use detailed=true to get entities with relationships
    if not detailed:
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "data": product_list
        }

    result = []
    for product_item in product_list:
        observations_ids = database.query(Observation.id).filter(Observation.product_id == product_item.code).all()
        children_ids = database.query(Product.code).filter(Product.parent_id == product_item.code).all()
        item_data = {
            "product": product_item,
            "observations_ids": [x[0] for x in observations_ids],            "children_ids": [x[0] for x in children_ids]        }
        result.append(item_data)
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "data": result
    }
@router.get("/product/search/", response_model=None, tags=["Product"])
def search_product(
    code: str = None,
    label: str = None,
    level: int = None,
    database: Session = Depends(get_db)
) -> list:
    """Search Product entities by attributes"""
    query = database.query(Product)

    if code is not None:
        query = query.filter(Product.code.ilike(f"%{code}%"))
    if label is not None:
        query = query.filter(Product.label.ilike(f"%{label}%"))
    if level is not None:
        query = query.filter(Product.level == level)

    results = query.all()
    return results


@router.get("/product/{product_id}/", response_model=None, tags=["Product"])
async def get_product(product_id: str, database: Session = Depends(get_db)) -> Product:
    db_product = database.query(Product).filter(Product.code == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    observations_ids = database.query(Observation.id).filter(Observation.product_id == db_product.code).all()
    children_ids = database.query(Product.code).filter(Product.parent_id == db_product.code).all()
    response_data = {
        "product": db_product,
        "observations_ids": [x[0] for x in observations_ids],        "children_ids": [x[0] for x in children_ids]}
    return response_data



@router.post("/product/", response_model=None, tags=["Product"])
async def create_product(product_data: ProductCreate, database: Session = Depends(get_db)) -> Product:


    db_product = Product(
        code=product_data.code,        label=product_data.label,        level=product_data.level        )

    database.add(db_product)
    database.flush()
    database.refresh(db_product)

    if product_data.observations:
        # Validate that all Observation IDs exist
        for observation_id in product_data.observations:
            db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
            if not db_observation:
                raise HTTPException(status_code=400, detail=f"Observation with id {observation_id} not found")

        # Update the related entities with the new foreign key
        database.query(Observation).filter(Observation.id.in_(product_data.observations)).update(
            {Observation.product_id: db_product.code}, synchronize_session=False
        )
        database.flush()
    if product_data.children:
        # Validate that all Product IDs exist
        for product_id in product_data.children:
            db_product = database.query(Product).filter(Product.code == product_id).first()
            if not db_product:
                raise HTTPException(status_code=400, detail=f"Product with id {product_id} not found")

        # Update the related entities with the new foreign key
        database.query(Product).filter(Product.code.in_(product_data.children)).update(
            {Product.parent_id: db_product.code}, synchronize_session=False
        )
        database.flush()



    observations_ids = database.query(Observation.id).filter(Observation.product_id == db_product.code).all()
    children_ids = database.query(Product.code).filter(Product.parent_id == db_product.code).all()
    response_data = {
        "product": db_product,
        "observations_ids": [x[0] for x in observations_ids],        "children_ids": [x[0] for x in children_ids]    }
    database.commit()
    database.refresh(db_product)
    return response_data


@router.post("/product/bulk/", response_model=None, tags=["Product"])
async def bulk_create_product(items: list[ProductCreate], database: Session = Depends(get_db)) -> dict:
    """Create multiple Product entities at once"""
    if any(getattr(item, field, None) for item in items for field in ['observations', 'children']):
        raise HTTPException(status_code=422, detail="Bulk creation of Product with relationships is not supported; use POST /product/ to validate and persist its relationships")
    created_items = []
    errors = []

    for idx, item_data in enumerate(items):
        try:
            # Basic validation for each item

            db_product = Product(
                code=item_data.code,                label=item_data.label,                level=item_data.level            )
            database.add(db_product)
            database.flush()  # Get ID without committing
            created_items.append(db_product.code)
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})

    if errors:
        database.rollback()
        raise HTTPException(status_code=400, detail={"message": "Bulk creation failed", "errors": errors})

    database.commit()
    return {
        "created_count": len(created_items),
        "created_ids": created_items,
        "message": f"Successfully created {len(created_items)} Product entities"
    }


@router.delete("/product/bulk/", response_model=None, tags=["Product"])
async def bulk_delete_product(ids: list[str], database: Session = Depends(get_db)) -> dict:
    """Delete multiple Product entities at once"""
    # Aggregate deletion must account for every related lower bound. Until it
    # shares that validation, use the guarded individual delete endpoint.
    if ids:
        raise HTTPException(status_code=409, detail="Bulk deletion of Product can violate required relationships and is not supported; use the individual DELETE /product/ endpoint")
    deleted_count = 0
    not_found = []

    for item_id in ids:
        db_product = database.query(Product).filter(Product.code == item_id).first()
        if db_product:
            database.delete(db_product)
            deleted_count += 1
        else:
            not_found.append(item_id)

    database.commit()

    return {
        "deleted_count": deleted_count,
        "not_found": not_found,
        "message": f"Successfully deleted {deleted_count} Product entities"
    }

@router.put("/product/{product_id}/", response_model=None, tags=["Product"])
async def update_product(product_id: str, product_data: ProductCreate, database: Session = Depends(get_db)) -> Product:
    db_product = database.query(Product).filter(Product.code == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    setattr(db_product, 'label', product_data.label)
    setattr(db_product, 'level', product_data.level)
    if product_data.observations is not None:
        requested_observations = set(product_data.observations)
        current_observations = {
            getattr(item, 'id')
            for item in database.query(Observation).filter(Observation.product_id == db_product.code).all()
        }
        observations_to_detach = current_observations - requested_observations
        if observations_to_detach:
            raise HTTPException(status_code=409, detail="Observation " + ", ".join(str(item) for item in sorted(observations_to_detach)) + " requires a product: reassign it instead of removing it")
        observations_to_attach = requested_observations - current_observations
        for observation_id in observations_to_attach:
            db_observation = database.query(Observation).filter(Observation.id == observation_id).first()
            if not db_observation:
                raise HTTPException(status_code=400, detail="Observation with id " + str(observation_id) + " not found")
        if observations_to_attach:
            database.query(Observation).filter(Observation.id.in_(observations_to_attach)).update(
                {Observation.product_id: db_product.code}, synchronize_session=False
            )
    if product_data.children is not None:
        requested_children = set(product_data.children)
        current_children = {
            getattr(item, 'code')
            for item in database.query(Product).filter(Product.parent_id == db_product.code).all()
        }
        children_to_detach = current_children - requested_children
        if children_to_detach:
            database.query(Product).filter(Product.code.in_(children_to_detach)).update(
                {Product.parent_id: None}, synchronize_session=False
            )
        children_to_attach = requested_children - current_children
        for product_id in children_to_attach:
            db_product = database.query(Product).filter(Product.code == product_id).first()
            if not db_product:
                raise HTTPException(status_code=400, detail="Product with id " + str(product_id) + " not found")
        if children_to_attach:
            database.query(Product).filter(Product.code.in_(children_to_attach)).update(
                {Product.parent_id: db_product.code}, synchronize_session=False
            )
    database.commit()
    database.refresh(db_product)

    observations_ids = database.query(Observation.id).filter(Observation.product_id == db_product.code).all()
    children_ids = database.query(Product.code).filter(Product.parent_id == db_product.code).all()
    response_data = {
        "product": db_product,
        "observations_ids": [x[0] for x in observations_ids],        "children_ids": [x[0] for x in children_ids]    }
    return response_data


@router.delete("/product/{product_id}/", response_model=None, tags=["Product"])
async def delete_product(product_id: str, database: Session = Depends(get_db)):
    db_product = database.query(Product).filter(Product.code == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    related_observations = db_product.observations
    for related in (list(related_observations) if isinstance(related_observations, list) else [item for item in [related_observations] if item is not None]):
        remaining = 0
        if remaining < 1:
            raise HTTPException(status_code=409, detail="Cannot delete Product " + str(product_id) + ": Observation " + str(getattr(related, 'id')) + " requires at least 1 product")
    # Snapshot the columns before deleting: cascading the association-class
    # links loads relationship collections whose back-references would make
    # the JSON encoder recurse endlessly on the live object.
    deleted_product = {
        attr.key: getattr(db_product, attr.key)
        for attr in db_product.__mapper__.column_attrs
    }
    database.delete(db_product)
    database.commit()
    return deleted_product


@router.get("/product/{product_id}/observations/", response_model=None, tags=["Product Relationships"])
async def get_observations_of_product(product_id: str, database: Session = Depends(get_db)):
    """Get all Observation entities related to this Product through observations"""
    db_product = database.query(Product).filter(Product.code == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    observations_list = database.query(Observation).filter(Observation.product_id == product_id).all()

    return {
        "product_id": product_id,
        "observations_count": len(observations_list),
        "observations": observations_list
    }

@router.get("/product/{product_id}/children/", response_model=None, tags=["Product Relationships"])
async def get_children_of_product(product_id: str, database: Session = Depends(get_db)):
    """Get all Product entities related to this Product through children"""
    db_product = database.query(Product).filter(Product.code == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    children_list = database.query(Product).filter(Product.parent_id == product_id).all()

    return {
        "product_id": product_id,
        "children_count": len(children_list),
        "children": children_list
    }


