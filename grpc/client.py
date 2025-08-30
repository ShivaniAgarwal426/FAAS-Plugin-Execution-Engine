import grpc
import initialize_pb2
import initialize_pb2_grpc

def run():
    # Connect to the server
    channel = grpc.insecure_channel('localhost:50051')
    stub = initialize_pb2_grpc.GreeterStub(channel)

    # Make a request
    response = stub.SayHello(initialize_pb2.HelloRequest(name="Pratik"))
    print("Server Response:", response.message)

if __name__ == "__main__":
    run()
