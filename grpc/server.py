import grpc
from concurrent import futures
import time
import initialize_pb2
import initialize_pb2_grpc

# Implement the service
class GreeterServicer(initialize_pb2_grpc.GreeterServicer):
    def SayHello(self, request, context):
        return initialize_pb2.HelloReply(message=f"Hello, {request.name}!")

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    initialize_pb2_grpc.add_GreeterServicer_to_server(GreeterServicer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    print("Server started on port 50051...")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == "__main__":
    serve()
