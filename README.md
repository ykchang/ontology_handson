# Ontology & Graph RAG Hands On Lab. Created by Dover Enc

# Requirements

이 Hands On Lab은 Oracle Virtual Box의 가상머신에 docker 기반으로 실습하도록 되어 있다.

Host 컴퓨터 요구 사항

- 메모리 : 16GB 이상 -> 실행해야 할 docker container가 많아서 메모리 소모가 많음
- 디스크 : 50 GB 이상 여유 공간 
- Windows, Linux (RedHat 계열, Debian 계역), macOS 지원
- VM인 관계로 로컬 호스트에 설치되어 있는 GPU의 도움을 받을 수 없음

사전에 [Oracle Virtual Box]를 설치해야 한다

- https://www.virtualbox.org/wiki/Downloads
- OS별 설치 방법 및 Virtual Box Extension Pack 설치 방법은 해당 화면의 Documents를 참조한다.


# Hands On Lab 진행

- 다운로드 받은 Repository의 doc 폴더에 HandsOn을 진행 할 수 있는 handsonlab_script.txt와 PDF 파일 포함하고 있음
- Virtual Box VM은 용량 문제로 별도 공간에 보관함

- Hands On Lab은 실습 자료인 관계로 Ontology, Ollama 등 학술적 설명은 하지 않음
- Ubuntu Linux, Docker에 대한 사용 방법은 포함하지 않음
- Spring Boot 자바 코드에 대해서 설명하지 않음


# Docker Compose 파일 수정

- Docker Compose 파일 중 일부는 HOST 파일의 IP ADDRESS로 변경해야 함.
- YML 파일을 열었을 때 DOCKER_HOST_IP 로 표시되었거나 127.0.0.1로 된 것은 Virtual Box 가상 머신의 할당된 IP로 변경해야 함.
  . 127.0.0.1, localhost 등으로 설정 할 경우 동작 안 될 수도 있음 (JMX, 원격에서 KAFKA 접근 등)


# 참고 자료

- 본 Hands On Lab의 가상 머신은 Ubuntu 24.04 LTS 버전을 기준으로 작성되었음

[Oracle Virtual Box]: https://www.virtualbox.org/