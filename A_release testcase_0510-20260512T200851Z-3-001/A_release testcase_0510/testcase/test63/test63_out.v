module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n31,
    n32,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test63/test63.v:10$4_Y , \$or$testcase/test63/test63.v:11$6_Y , \$xor$testcase/test63/test63.v:12$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n114, n120, n121, n122, n123, n128, n129, n130;

  and   \$and$testcase/test63/test63.v:14$10  (n107, n2, n3);
  and   \$and$testcase/test63/test63.v:28$19  (n120, n2, n3);
  and   \$and$testcase/test63/test63.v:6$1  (n100, n2, n3);
  and   \$and$testcase/test63/test63.v:29$20  (n121, n2, n4);
  not   \$not$testcase/test63/test63.v:22$31  (n114, n4);
  and   \$and$testcase/test63/test63.v:30$21  (n122, n2, n5);
  and   \$and$testcase/test63/test63.v:31$22  (n123, n2, n6);
  or    \$or$testcase/test63/test63.v:15$11  (n108, n107, n5);
  or    \$or$testcase/test63/test63.v:7$2  (n101, n100, n4);
  or    \$or$testcase/test63/test63.v:32$23  (n128, n120, n121);
  or    \$or$testcase/test63/test63.v:24$15  (n22, n114, n100);
  xor   \$xor$testcase/test63/test63.v:33$24  (n129, n122, n123);
  xor   \$xor$testcase/test63/test63.v:16$12  (n21, n108, n6);
  not   \$not$testcase/test63/test63.v:8$28  (n102, n101);
  and   \$and$testcase/test63/test63.v:38$27  (n130, n129, n128);
  xor   \$xor$testcase/test63/test63.v:9$3  (n103, n102, n5);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  and   \$and$testcase/test63/test63.v:10$4  (\$and$testcase/test63/test63.v:10$4_Y , n103, n6);
  not   \$not$testcase/test63/test63.v:10$5  (n104, \$and$testcase/test63/test63.v:10$4_Y );
  or    \$or$testcase/test63/test63.v:11$6  (\$or$testcase/test63/test63.v:11$6_Y , n104, n7);
  not   \$not$testcase/test63/test63.v:11$7  (n105, \$or$testcase/test63/test63.v:11$6_Y );
  xor   \$xor$testcase/test63/test63.v:12$8  (\$xor$testcase/test63/test63.v:12$8_Y , n105, n8);
  not   \$not$testcase/test63/test63.v:12$9  (n20, \$xor$testcase/test63/test63.v:12$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = n5;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
