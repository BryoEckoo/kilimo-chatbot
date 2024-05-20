from datetime import datetime, timezone
from enum import Enum
import os
import pickle
from time import sleep
from typing import Any, Dict, List, Optional
import uuid

from qdrant_client import QdrantClient
from qdrant_client.conversions import common_types as types
from qdrant_client.models import Distance as QdrantDist
from qdrant_client.models import PointStruct, PointVectors, VectorParams

from seaplane.config import config


class Vector:
    def __init__(
        self,
        vector: List[float],
        id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.vector = vector
        self.metadata = metadata
        if id is None:
            self.id = str(uuid.uuid4())
        else:
            self.id = id

    def __repr__(self) -> str:
        return f"Vector(vector={self.vector}, id={self.id}," f" metadata={self.metadata})"


Vectors = List[Vector]


class Distance(Enum):
    COSINE = "cosine"
    DOT = "dot"
    EUCLID = "euclid"

    def __str__(self) -> str:
        return str(self.value)


class VectorStore:
    def __init__(self) -> None:
        self.client: Optional[QdrantClient] = None
        self.TIME_OUT_IN_SECONDS = 30
        pass

    def _get_client(self) -> Optional[QdrantClient]:
        if not self.client:
            self.connect()

        return self.client

    def _get_auth_header(self, force_renew: bool = False) -> str:
        token = config._token_api.access_token
        if not token or force_renew:
            token = config._token_api.renew_token()

        return f"Bearer {token}"

    def connect(self) -> QdrantClient:
        if config.debug_endpoint is not None:
            ret = QdrantClient(
                url=config.vector_db_endpoint,
                timeout=self.TIME_OUT_IN_SECONDS,
            )
            self.client = ret
            return ret

        ret = QdrantClient(
            url=config.vector_db_endpoint,
            port=443,
            timeout=self.TIME_OUT_IN_SECONDS,
        )

        def auth_middleware(request: Any, call_next: Any) -> Any:
            request.headers["Authorization"] = self._get_auth_header()
            response = call_next(request)

            if response.status_code == 401:
                request.headers["Authorization"] = self._get_auth_header(force_renew=True)
                response = call_next(request)

            return response

        http_client = ret.http.client
        http_client.add_middleware(auth_middleware)

        self.client = ret

        return ret

    def map_status(self, status: Any) -> str:
        return str(status).removeprefix("UpdateStatus.")

    def check_connection(self) -> QdrantClient:
        if not self.client:
            client = self.connect()
        else:
            client = self.client

        return client

    def create_index(
        self,
        name: str,
        vector_size: int = 768,
        distance: Distance = Distance.COSINE,
        if_not_exists: Optional[bool] = False,
    ) -> bool:
        client = self.check_connection()

        if if_not_exists:
            index_list = self.list_indexes()
            if name in index_list:
                return True

        return client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=self._map_to_qdrant_distance(distance),
            ),
            timeout=self.TIME_OUT_IN_SECONDS,
            replication_factor=3 if config.debug_endpoint is None else None,
            write_consistency_factor=2 if config.debug_endpoint is None else None,
        )

    def recreate_index(
        self,
        name: str,
        vector_size: int = 768,
        distance: Distance = Distance.COSINE,
    ) -> bool:
        client = self.check_connection()

        return client.recreate_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=vector_size,
                distance=self._map_to_qdrant_distance(distance),
            ),
            timeout=self.TIME_OUT_IN_SECONDS,
            replication_factor=3 if config.debug_endpoint is None else None,
            write_consistency_factor=2 if config.debug_endpoint is None else None,
        )

    def delete_index(self, name: str) -> bool:
        client = self.check_connection()

        return client.delete_collection(collection_name=name, timeout=self.TIME_OUT_IN_SECONDS)

    def list_indexes(self) -> List[str]:
        client = self.check_connection()

        collections = client.get_collections()
        return [idx.name for idx in collections.collections]

    def insert(self, index_name: str, vectors: Vectors) -> Dict[str, Any]:
        client = self.check_connection()

        ids = [vector.id for vector in vectors]

        result = client.upsert(
            collection_name=index_name,
            points=[
                PointStruct(id=vector.id, vector=vector.vector, payload=vector.metadata)
                for vector in vectors
            ],
        )

        return {"id": ids, "status": self.map_status(result.status)}

    def delete(self, index_name: str, points_selector: types.PointsSelector) -> str:
        """Delete Points from an index (collection)

        Points contain both vectors and metadata.
        This is the preferred method for removing data.
        """
        client = self.check_connection()

        result = client.delete(collection_name=index_name, points_selector=points_selector)

        return self.map_status(result.status)

    def delete_vectors(self, index_name: str, id: List[str]) -> str:
        """Delete vectors from an index (collection)

        This only deletes the vector, leaving the metadata behind.
        You should probably use the delete method instead!
        """
        client = self.check_connection()

        result = client.delete_vectors(collection_name=index_name, vectors=[""], points=id)

        return self.map_status(result.status)

    def update(self, index_name: str, vectors: Vectors) -> Dict[str, Any]:
        client = self.check_connection()

        ids = [vector.id for vector in vectors]

        result = client.update_vectors(
            collection_name=index_name,
            points=[PointVectors(id=vector.id, vector=vector.vector) for vector in vectors],
        )

        return {"id": ids, "status": self.map_status(result.status)}

    def _map_to_qdrant_distance(self, distance: Distance) -> QdrantDist:
        if distance == Distance.COSINE:
            return QdrantDist.COSINE
        elif distance == Distance.EUCLID:
            return QdrantDist.EUCLID
        elif distance == Distance.DOT:
            return QdrantDist.DOT
        else:
            return QdrantDist.COSINE

    def knn_search(
        self,
        index_name: str,
        vector: Vector,
        neighbors: int = 10,
        query_filter: Optional[types.Filter] = None,
    ) -> object:
        client = self.check_connection()

        if query_filter is not None:
            return client.search(
                collection_name=index_name,
                query_vector=vector.vector,
                limit=neighbors,
                query_filter=query_filter,
            )
        return client.search(
            collection_name=index_name,
            query_vector=vector.vector,
            limit=neighbors,
        )

    def recommend(
        self,
        index_name: str,
        positive: Optional[List[types.RecommendExample]] = None,
        negative: Optional[List[types.RecommendExample]] = None,
        neighbors: int = 10,
        strategy: Optional[types.RecommendStrategy] = None,
        query_filter: Optional[types.Filter] = None,
    ) -> object:
        client = self.check_connection()

        return client.recommend(
            collection_name=index_name,
            positive=positive,
            negative=negative,
            limit=neighbors,
            strategy=strategy,
            query_filter=query_filter,
        )

    def backup(
        self,
        index_name: str,
        path: str,
    ) -> None:
        """Backup vectors to pickle format in a given local directory"""
        client = self.check_connection()

        timestamp = datetime.now(timezone.utc).astimezone()
        os.makedirs(path, exist_ok=True)
        backup_name = f"{index_name}_{timestamp.isoformat()}"

        print(f"Backing up {int(client.count(index_name).count)} vectors to {path}")
        counter = 0
        next_offset: int | str | Any | None = 0

        while next_offset is not None:
            records, next_offset = client.scroll(
                index_name, offset=next_offset, limit=100, with_vectors=True
            )
            backup_file = f"{backup_name}_{counter}.backup"
            backup_filepath = os.path.join(path, backup_file)
            with open(backup_filepath, "wb") as backpath:
                pickle.dump(records, backpath, protocol=pickle.HIGHEST_PROTOCOL)
            counter += 1

        print(f"Backed up vector store {index_name}.")

    def restore(
        self,
        index_name: str,
        path: str,
    ) -> None:
        """Restore vectors from pickle backup"""

        client = self.check_connection()
        self.create_index(name=index_name, if_not_exists=True)

        def vect_count() -> int:
            return int(client.count(index_name).count)

        if os.path.exists(path):
            files = os.scandir(path)
            vector_count = 0
            for file in files:
                if os.path.splitext(file)[-1].lower() == ".backup":
                    with open(file, "rb") as backup:
                        data = pickle.load(backup)
                        vector_list: Vectors = []
                        for record in data:
                            this_vector = Vector(
                                id=record.id, vector=record.vector, metadata=record.payload
                            )
                            vector_list.append(this_vector)
                            vector_count += 1

                        print(f"Attempting to restore {vector_count} vectors to {index_name}")
                        self.insert(index_name, vector_list)

            # Ensure the restore is complete while reporting progress.
            while vector_count > vect_count():
                print(f"Restored {vect_count()} out of {vector_count} vectors")
                sleep(1)

            print(f"Finished restoring {vector_count} vectors to {index_name}")
        else:
            print(f"Path {path} does not exist")


vector_store = VectorStore()
